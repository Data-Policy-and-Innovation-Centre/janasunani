"""Real-code-path tests for the infra status pass.

Drives the actual evaluation functions in `scripts/infra_status.py` with
AWS-shaped payloads, rather than mocking the module. The collectors shell out
to `aws`/`ssh`/`curl`, which cannot run here and must never be pointed at
production from a test, so the split is deliberate: collection is thin and
dumb, every judgement lives in a pure function, and the judgements are what
get tested.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from janasunani.config import ROOT_DIR

_spec = importlib.util.spec_from_file_location(
    "infra_status", Path(ROOT_DIR) / "scripts" / "infra_status.py"
)
infra_status = importlib.util.module_from_spec(_spec)
# scripts/ is not a package; register before exec so @dataclass can resolve
# the module (matches tests/test_bootstrap_pii_gold.py)
sys.modules[_spec.name] = infra_status
_spec.loader.exec_module(infra_status)

CRIT, WARN, OK, INFO = (
    infra_status.CRIT,
    infra_status.WARN,
    infra_status.OK,
    infra_status.INFO,
)


def _ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


# --- compute ------------------------------------------------------------------


def test_cpu_box_not_running_is_critical():
    """It is always-on and holds the production Postgres."""
    stopped = {"State": {"Name": "stopped"}, "InstanceType": "t3.large"}
    assert infra_status.evaluate_instance("cpu box", stopped, always_on=True).status == CRIT


def test_cpu_box_running_is_ok():
    running = {"State": {"Name": "running"}, "InstanceType": "t3.large"}
    finding = infra_status.evaluate_instance("cpu box", running, always_on=True)
    assert finding.status == OK
    assert "t3.large" in finding.detail


def test_missing_cpu_box_is_critical_but_missing_gpu_box_is_not():
    """gpu_box_count = 0 is the normal resting state; a missing CPU box is not."""
    assert infra_status.evaluate_instance("cpu box", None, always_on=True).status == CRIT
    assert infra_status.evaluate_instance("gpu box", None, always_on=False).status == OK


def test_running_gpu_box_warns_because_it_bills_by_the_hour():
    running = {
        "State": {"Name": "running"},
        "InstanceType": "g5.xlarge",
        "LaunchTime": _ago(3),
    }
    finding = infra_status.evaluate_instance("gpu box", running, always_on=False)
    assert finding.status == WARN
    assert "billing" in finding.detail
    assert "3.0h" in finding.detail


def test_stopped_gpu_box_is_ok():
    stopped = {"State": {"Name": "stopped"}, "InstanceType": "g5.xlarge"}
    assert infra_status.evaluate_instance("gpu box", stopped, always_on=False).status == OK


# --- network (issue #32) ------------------------------------------------------


def test_no_ssh_ingress_is_ok():
    findings = infra_status.evaluate_ssh_exposure([])
    assert [f.status for f in findings] == [OK]


def test_world_open_ssh_is_critical():
    permissions = [{"FromPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == CRIT
    assert "production PII" in findings[0].detail


def test_leftover_runner_rule_warns_and_cites_the_issue():
    """A /32 left behind means a deploy runner died between authorize and
    revoke — the leak half of #32."""
    permissions = [
        {
            "FromPort": 22,
            "IpRanges": [{"CidrIp": "10.1.2.3/32", "Description": "ci-deploy"}],
        }
    ]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == WARN
    assert "#32" in findings[0].detail
    assert "ci-deploy" in findings[0].detail


def test_non_ssh_ports_are_ignored():
    permissions = [{"FromPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert [f.status for f in findings] == [OK], "443 open to the world is the proxy, by design"


# --- disk ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "free_gib,expected",
    [(5.0, CRIT), (19.9, CRIT), (20.1, WARN), (39.0, WARN), (80.0, OK), (None, INFO)],
)
def test_disk_thresholds_match_deploy_sh(free_gib, expected):
    """20 GiB is deploy.sh's own MIN_FREE_KIB floor, not an invented number."""
    assert infra_status.evaluate_disk(free_gib).status == expected


def test_critical_disk_explains_the_blast_radius():
    detail = infra_status.evaluate_disk(3.0).detail
    assert "Postgres" in detail, "a disk-full takes prod down, not just the deploy"


# --- containers ---------------------------------------------------------------


def test_all_containers_running_is_ok():
    findings = infra_status.evaluate_containers(list(infra_status.STACK_CONTAINERS))
    assert all(f.status == OK for f in findings)
    assert len(findings) == len(infra_status.STACK_CONTAINERS)


def test_a_missing_container_is_critical():
    running = [c for c in infra_status.STACK_CONTAINERS if c != "janasunani-api"]
    findings = infra_status.evaluate_containers(running)
    by_name = {f.name: f for f in findings}
    assert by_name["janasunani-api"].status == CRIT
    assert by_name["janasunani-proxy"].status == OK


def test_no_containers_reads_as_not_deployed_not_as_broken():
    """Before the first deploy the box legitimately has no stack."""
    findings = infra_status.evaluate_containers([])
    assert [f.status for f in findings] == [INFO]
    assert "not deployed" in findings[0].detail


# --- backups (issue #31) ------------------------------------------------------


@pytest.mark.parametrize(
    "hours,expected", [(2, OK), (25, OK), (30, WARN), (60, CRIT)]
)
def test_backup_staleness_thresholds(hours, expected):
    assert infra_status.evaluate_backup(_ago(hours), 1_000_000_000).status == expected


def test_absent_backup_is_critical_and_names_the_reason():
    """The cron lives only on the box, so a rebuild silently loses it."""
    finding = infra_status.evaluate_backup(None, None)
    assert finding.status == CRIT
    assert "#31" in finding.detail


def test_backup_reports_size():
    detail = infra_status.evaluate_backup(_ago(1), 2_500_000_000).detail
    assert "2.50 GB" in detail


def test_unparseable_backup_timestamp_warns_rather_than_crashing():
    assert infra_status.evaluate_backup("not-a-date", 1).status == WARN


# --- demo health --------------------------------------------------------------


def test_mock_processor_warns_even_though_the_site_is_up():
    """Healthy and responsive, serving canned results. This is the failure the
    history mock badge exists for, seen from the outside."""
    finding = infra_status.evaluate_health({"status": "ok", "processor": "mock"}, None)
    assert finding.status == WARN
    assert "mock" in finding.detail


def test_real_processor_is_ok():
    finding = infra_status.evaluate_health({"status": "ok", "processor": "pipeline"}, None)
    assert finding.status == OK


def test_unreachable_health_is_critical():
    assert infra_status.evaluate_health(None, "unreachable").status == CRIT


# --- report aggregation -------------------------------------------------------


def test_exit_code_is_nonzero_only_for_critical():
    report = infra_status.Report()
    report.add("box", "disk", WARN, "")
    assert report.exit_code() == 0, "warnings must not fail a cron run"
    report.add("box", "stack", CRIT, "")
    assert report.exit_code() == 1


def test_worst_ignores_info():
    report = infra_status.Report()
    report.add("box", "ssh", INFO, "skipped")
    report.add("compute", "cpu box", OK, "running")
    assert report.worst == OK
    assert report.exit_code() == 0


def test_render_groups_by_section_and_summarizes():
    report = infra_status.Report()
    report.add("compute", "cpu box", OK, "running")
    report.add("box", "disk", CRIT, "3 GiB free")
    text = infra_status.render(report)
    assert "=== compute ===" in text and "=== box ===" in text
    assert "1 critical" in text


def test_render_says_all_clear_when_nothing_is_wrong():
    report = infra_status.Report()
    report.add("compute", "cpu box", OK, "running")
    assert "all clear" in infra_status.render(report)


def test_render_never_says_all_clear_when_nothing_was_checked():
    """--no-aws --no-ssh, unreachable AWS and an unreachable box all produce a
    report of pure INFO. Reading that as healthy is the exact mistake this tool
    exists to prevent."""
    report = infra_status.Report()
    report.add("compute", "aws", INFO, "skipped (--no-aws)")
    report.add("box", "ssh", INFO, "skipped (--no-ssh)")
    text = infra_status.render(report)
    assert "all clear" not in text
    assert "NOTHING CHECKED" in text


def test_render_counts_skips_alongside_real_results():
    report = infra_status.Report()
    report.add("compute", "cpu box", OK, "running")
    report.add("box", "ssh", INFO, "unreachable")
    text = infra_status.render(report)
    assert "all clear (1 checks)" in text
    assert "1 not checked" in text


# --- the read-only guarantee --------------------------------------------------


def test_script_runs_no_mutating_commands():
    """The whole design rests on this being a query-only tool. Asserted against
    the source so a later edit cannot quietly add a mutation."""
    source = (Path(ROOT_DIR) / "scripts" / "infra_status.py").read_text()
    # Mutating invocations only. `terraform output` and `aws ec2 describe-*`
    # are queries and are fine; the point is that nothing here changes state.
    forbidden = (
        "start-instances",
        "stop-instances",
        "terminate-instances",
        "authorize-security-group",
        "revoke-security-group",
        "docker compose",
        "docker rm",
        "docker stop",
        "docker prune",
        "s3 cp",
        "s3 rm",
        "s3 sync",
        "terraform apply",
        "terraform destroy",
        "rm -rf",
    )
    hits = [token for token in forbidden if token in source]
    assert not hits, f"infra_status must stay read-only; found {hits}"


def test_parser_defaults_skip_nothing_but_require_opt_in_for_targets():
    args = infra_status.build_parser().parse_args([])
    assert args.no_ssh is False and args.no_aws is False
    # site and sg-id have no safe default: guessing them would either check the
    # wrong account's resources or silently report "not checked" as healthy
    assert args.site is None and args.sg_id is None
