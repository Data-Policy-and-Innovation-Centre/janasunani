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
    permissions = [
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == CRIT
    assert "production PII" in findings[0].detail


def test_leftover_runner_rule_warns_and_cites_the_issue():
    """A /32 left behind means a deploy runner died between authorize and
    revoke — the leak half of #32."""
    permissions = [
        {
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "IpRanges": [{"CidrIp": "10.1.2.3/32", "Description": "ci-deploy"}],
        }
    ]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == WARN
    assert "#32" in findings[0].detail
    assert "ci-deploy" in findings[0].detail


def test_non_ssh_ports_are_ignored():
    permissions = [
        {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert [f.status for f in findings] == [OK], "443 open to the world is the proxy, by design"


def test_all_protocols_wildcard_is_seen_as_ssh_exposure():
    """IpProtocol="-1" is AWS's all-protocols-all-ports rule. It has no
    meaningful FromPort/ToPort, and a guard keyed on FromPort==22 misses it
    entirely — the exact P1 Codex flagged on #88."""
    permissions = [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == CRIT
    assert "production PII" in findings[0].detail


def test_wide_tcp_port_range_covering_22_is_seen_as_ssh_exposure():
    """FromPort=0, ToPort=65535 covers 22 exactly as much as FromPort=22,
    ToPort=22 — a range, not a single port, per AWS's IpPermission."""
    permissions = [
        {
            "IpProtocol": "tcp",
            "FromPort": 0,
            "ToPort": 65535,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
        }
    ]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == CRIT


def test_ipv6_ssh_source_is_checked_too():
    permissions = [
        {
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
        }
    ]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == CRIT
    assert "::/0" in findings[0].detail


def test_maintainer_rule_reads_ok_not_as_a_leftover():
    """deploy/terraform/main.tf's standing admin rule must let a healthy box
    render all clear, distinct from a leftover deploy-runner /32 (#32)."""
    permissions = [
        {
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "IpRanges": [
                {
                    "CidrIp": "203.0.113.4/32",
                    "Description": infra_status.MAINTAINER_SSH_DESCRIPTION,
                }
            ],
        }
    ]
    findings = infra_status.evaluate_ssh_exposure(permissions)
    assert findings[0].status == OK
    assert "maintainer" in findings[0].detail


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


def test_oltp_only_reads_as_pre_deploy_not_as_three_missing_containers():
    """docs/DEPLOY.md's documented first-time state (`up -d oltp` only,
    before the app images are deployed) must not CRIT api/frontend/proxy --
    they were never asked to start. Codex review on #88."""
    findings = infra_status.evaluate_containers(["janasunani-oltp"])
    assert [f.status for f in findings] == [INFO]
    assert "not deployed" in findings[0].detail


def test_oltp_plus_one_more_is_a_real_partial_deploy():
    """Once more than just oltp is up, a missing container is a genuine gap,
    not the documented pre-deploy state."""
    findings = infra_status.evaluate_containers(["janasunani-oltp", "janasunani-api"])
    by_name = {f.name: f for f in findings}
    assert by_name["janasunani-oltp"].status == OK
    assert by_name["janasunani-api"].status == OK
    assert by_name["janasunani-frontend"].status == CRIT
    assert by_name["janasunani-proxy"].status == CRIT


def test_running_but_unhealthy_container_is_critical():
    """A container Docker itself has marked failing its HEALTHCHECK
    (oltp/api/frontend all define one) must not read as OK just because a
    name-only `docker ps` still lists it. Codex review on #88."""
    findings = infra_status.evaluate_containers(
        list(infra_status.STACK_CONTAINERS), unhealthy=frozenset({"janasunani-api"})
    )
    by_name = {f.name: f for f in findings}
    assert by_name["janasunani-api"].status == CRIT
    assert "HEALTHCHECK" in by_name["janasunani-api"].detail
    assert by_name["janasunani-oltp"].status == OK


def test_running_and_healthy_stays_ok_when_unhealthy_set_is_empty():
    findings = infra_status.evaluate_containers(
        list(infra_status.STACK_CONTAINERS), unhealthy=frozenset()
    )
    assert all(f.status == OK for f in findings)


def test_unhealthy_param_defaults_to_none_and_is_treated_as_no_unhealthy_containers():
    """Existing callers that don't pass `unhealthy` (e.g. a pre-`docker ps
    -a`-format collector) must keep working exactly as before."""
    findings = infra_status.evaluate_containers(list(infra_status.STACK_CONTAINERS))
    assert all(f.status == OK for f in findings)


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


def test_empty_backup_object_is_critical_even_when_fresh():
    """A 0-byte object after a failed `pg_dump | aws s3 cp` must not read as
    healthy just because it is recent -- there is nothing restorable in it.
    Codex review on #88."""
    finding = infra_status.evaluate_backup(_ago(0.1), 0)
    assert finding.status == CRIT
    assert "failed" in finding.detail


def test_near_empty_backup_object_is_critical():
    finding = infra_status.evaluate_backup(_ago(0.1), 512)
    assert finding.status == CRIT


def test_backup_of_unknown_size_is_judged_on_freshness_only():
    """S3 not reporting a size is not itself evidence of a failed dump --
    don't invent a size-based CRIT out of missing data."""
    finding = infra_status.evaluate_backup(_ago(1), None)
    assert finding.status == OK


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


def test_unrecognized_processor_warns_not_ok():
    """Only "pipeline" (PipelineGrievanceProcessor.name) and "mock"
    (MockGrievanceProcessor.name) are values this codebase can actually
    produce. Anything else -- a malformed, renamed, or wrong API -- must not
    read as a healthy demo just because it isn't literally "mock". Codex
    review on #88."""
    finding = infra_status.evaluate_health({"status": "ok", "processor": "bogus"}, None)
    assert finding.status == WARN
    assert "bogus" in finding.detail


def test_missing_processor_field_warns_not_ok():
    finding = infra_status.evaluate_health({"status": "ok"}, None)
    assert finding.status == WARN


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


def test_nothing_checked_true_only_when_every_finding_is_info():
    """`worst` reads OK on a pure-INFO report by design (see its docstring);
    `nothing_checked` is the separate signal `--json` needs so a monitor
    cannot mistake a fully-skipped run for a healthy one. Codex review on
    #88 flagged `--json` emitting `"worst": "OK"` for exactly this case."""
    report = infra_status.Report()
    report.add("compute", "aws", INFO, "skipped (--no-aws)")
    report.add("box", "ssh", INFO, "skipped (--no-ssh)")
    assert report.nothing_checked is True
    assert report.checked_count == 0
    assert report.skipped_count == 2
    assert report.worst == OK  # unchanged, deliberate -- see docstring

    report.add("compute", "cpu box", OK, "running")
    assert report.nothing_checked is False
    assert report.checked_count == 1
    assert report.skipped_count == 2


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


def test_render_qualifies_all_clear_when_some_checks_did_not_run():
    """A partial pass is not a pass. "all clear" alone would invite reading an
    unreachable box as a healthy one."""
    report = infra_status.Report()
    report.add("compute", "cpu box", OK, "running")
    report.add("box", "ssh", INFO, "unreachable")
    text = infra_status.render(report)
    assert "were not run" in text
    assert "all clear on 1 checks" in text


# --- collection: a failed AWS call is not the same as an empty result ---------
#
# Codex review on #88: a failed `describe-instances`/`list-objects-v2` call
# (bad credentials, wrong region, IAM, network) collapsed into the same "not
# found"/"no objects" result as a *successful* call that legitimately found
# nothing, raising a false production alarm instead of degrading to "not
# checked". These monkeypatch `_aws_json` (the one seam between the collectors
# and the actual `aws` subprocess) rather than shelling out for real, keeping
# with the file's collection-is-thin-collectors-are-dumb split.


def test_collect_instances_returns_none_when_the_aws_call_fails(monkeypatch):
    monkeypatch.setattr(infra_status, "_aws_json", lambda args, region: None)
    assert infra_status.collect_instances({"cpu box": "cpu"}, "ap-south-1") is None


def test_collect_instances_returns_not_found_when_the_call_succeeds_empty(monkeypatch):
    monkeypatch.setattr(infra_status, "_aws_json", lambda args, region: {"Reservations": []})
    found = infra_status.collect_instances({"cpu box": "cpu"}, "ap-south-1")
    assert found == {"cpu box": None}


def test_collect_backup_returns_none_when_the_aws_call_fails(monkeypatch):
    monkeypatch.setattr(infra_status, "_aws_json", lambda args, region: None)
    assert infra_status.collect_backup("ap-south-1") is None


def test_collect_backup_returns_no_objects_when_the_call_succeeds_empty(monkeypatch):
    monkeypatch.setattr(infra_status, "_aws_json", lambda args, region: {})
    assert infra_status.collect_backup("ap-south-1") == (None, None)


def test_aws_json_pins_the_region(monkeypatch):
    """An operator whose default CLI profile points at another region gets a
    *successful, empty* response, not an error -- indistinguishable from the
    box actually being gone unless the region is pinned explicitly."""
    captured: dict = {}

    def fake_run(command, timeout=30):
        captured["command"] = command
        return "{}"

    monkeypatch.setattr(infra_status, "_run", fake_run)
    infra_status._aws_json(["ec2", "describe-instances"], "ap-south-1")
    assert "--region" in captured["command"]
    assert "ap-south-1" in captured["command"]


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
