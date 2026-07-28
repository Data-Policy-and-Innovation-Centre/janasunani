"""One read-only status pass over the janasunani infrastructure.

Answers "is anything wrong right now" for the CPU box (which holds the
production Postgres, the models, the Parquet lake and the nightly `pg_dump`
target), the on-demand GPU box, the deployed demo stack, and the backups.

**Strictly read-only by construction.** Every command it runs is a query:
`aws ec2 describe-*`, `aws s3api list-objects-v2`, `df`, `docker ps`, and an
unauthenticated GET of `/api/health`. It never starts, stops, deploys, prunes
or writes anything. That is deliberate: this points at the box holding real
citizen data, and a status tool that can mutate is a status tool that will
eventually mutate at the wrong moment.

It also never prints a secret. The OLTP URL, the demo password and the DB
password are all out of scope for every check below.

Thresholds are taken from the repo, not invented:

* 20 GiB free disk is `deploy.sh`'s own `MIN_FREE_KIB` floor, below which it
  refuses to pull images (docs/DEPLOY.md §"Disk hygiene").
* Backups are nightly to `s3://grievance-database-backups-main/janasunani/`
  (docs/DEPLOY.md §5). That script lives only on the box and is **not**
  reproducible from code (issue #31), so a rebuilt box loses it silently —
  which is exactly why staleness is checked here.
* Port 22 exposure is issue #32's rule-leakage gap: the deploy workflow opens
  22 to the runner's /32 and revokes it after, and a runner that dies between
  the two leaves the rule behind.

Usage:

    uv run python scripts/infra_status.py
    uv run python scripts/infra_status.py --no-ssh        # AWS + HTTP only
    uv run python scripts/infra_status.py --json          # machine-readable

Exit code is 0 unless something is CRIT, so it is safe to put in a cron.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

# --- grounded constants ------------------------------------------------------

BACKUP_BUCKET = "grievance-database-backups-main"
BACKUP_PREFIX = "janasunani/"
# deploy.sh: min_free_kib="${MIN_FREE_KIB:-$((20 * 1024 * 1024))}"
DISK_CRIT_GIB = 20
DISK_WARN_GIB = 40
# nightly cadence, so one missed run is a warning and two is a failure
BACKUP_WARN_HOURS = 26
BACKUP_CRIT_HOURS = 48
STACK_CONTAINERS = (
    "janasunani-oltp",
    "janasunani-api",
    "janasunani-frontend",
    "janasunani-proxy",
)

OK, WARN, CRIT, INFO = "OK", "WARN", "CRIT", "INFO"
_RANK = {OK: 0, INFO: 0, WARN: 1, CRIT: 2}


@dataclass
class Finding:
    section: str
    name: str
    status: str
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, section: str, name: str, status: str, detail: str) -> None:
        self.findings.append(Finding(section, name, status, detail))

    @property
    def worst(self) -> str:
        """Highest severity present. INFO ranks with OK: "not checked" is not a
        problem, it is an absence of one, and must not read as degraded."""
        rank = max((_RANK[f.status] for f in self.findings), default=0)
        return {0: OK, 1: WARN, 2: CRIT}[rank]

    def exit_code(self) -> int:
        return 1 if self.worst == CRIT else 0


# --- evaluation (pure; this is what the tests drive) -------------------------


def evaluate_instance(name: str, instance: Optional[dict], *, always_on: bool) -> Finding:
    """One EC2 instance's state.

    The CPU box is always-on and holds production data, so anything but
    `running` is critical. The GPU box is on-demand and billed by the hour, so
    *running* is the noteworthy state.
    """
    section = "compute"
    if instance is None:
        if always_on:
            return Finding(section, name, CRIT, "not found (expected an always-on box)")
        return Finding(section, name, OK, "not provisioned (gpu_box_count = 0)")

    state = instance.get("State", {}).get("Name", "unknown")
    itype = instance.get("InstanceType", "?")
    if always_on:
        status = OK if state == "running" else CRIT
        return Finding(section, name, status, f"{state} ({itype})")

    if state == "running":
        launched = instance.get("LaunchTime")
        age = ""
        if launched:
            hours = _hours_since(launched)
            if hours is not None:
                age = f", up {hours:.1f}h"
        return Finding(
            section, name, WARN, f"running ({itype}{age}) — on-demand, still billing"
        )
    return Finding(section, name, OK, f"{state} ({itype})")


def evaluate_ssh_exposure(permissions: list[dict]) -> list[Finding]:
    """Port 22 ingress. See issue #32.

    A deploy authorizes 22 to the runner's /32 and revokes it afterwards. A
    leftover rule means a runner died mid-deploy; an open-to-the-world rule
    means something is badly wrong.
    """
    findings: list[Finding] = []
    for rule in permissions:
        if rule.get("FromPort") != 22:
            continue
        for cidr in rule.get("IpRanges", []):
            block = cidr.get("CidrIp", "?")
            if block in ("0.0.0.0/0", "::/0"):
                findings.append(
                    Finding(
                        "network",
                        "ssh exposure",
                        CRIT,
                        f"port 22 open to {block} — the box holds production PII",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "network",
                        "ssh exposure",
                        WARN,
                        f"port 22 open to {block}"
                        f"{' (' + cidr['Description'] + ')' if cidr.get('Description') else ''}"
                        " — expected only during a deploy; a leftover rule is issue #32",
                    )
                )
    if not findings:
        findings.append(Finding("network", "ssh exposure", OK, "no port-22 ingress"))
    return findings


def evaluate_disk(free_gib: Optional[float]) -> Finding:
    """Free space on the box's root volume.

    That one volume carries prod Postgres, the models, the lake, the HF cache
    and the pg_dump target, so filling it takes all of them down together, not
    just the next deploy.
    """
    if free_gib is None:
        return Finding("box", "disk", INFO, "not checked")
    if free_gib < DISK_CRIT_GIB:
        return Finding(
            "box",
            "disk",
            CRIT,
            f"{free_gib:.1f} GiB free — below deploy.sh's {DISK_CRIT_GIB} GiB floor; "
            "prod Postgres shares this volume",
        )
    if free_gib < DISK_WARN_GIB:
        return Finding("box", "disk", WARN, f"{free_gib:.1f} GiB free")
    return Finding("box", "disk", OK, f"{free_gib:.1f} GiB free")


def evaluate_containers(running: Optional[list[str]]) -> list[Finding]:
    if running is None:
        return [Finding("box", "stack", INFO, "not checked")]
    if not running:
        return [
            Finding(
                "box", "stack", INFO, "no janasunani containers — stack not deployed yet"
            )
        ]
    findings = []
    for name in STACK_CONTAINERS:
        up = name in running
        findings.append(
            Finding("box", name, OK if up else CRIT, "running" if up else "not running")
        )
    return findings


def evaluate_backup(last_modified: Optional[str], size_bytes: Optional[int]) -> Finding:
    """Freshness of the newest nightly pg_dump in S3."""
    if last_modified is None:
        return Finding(
            "backups",
            "pg_dump",
            CRIT,
            f"no objects under s3://{BACKUP_BUCKET}/{BACKUP_PREFIX} — "
            "the cron lives only on the box and is not in code (issue #31)",
        )
    hours = _hours_since(last_modified)
    if hours is None:
        return Finding("backups", "pg_dump", WARN, f"unparseable timestamp {last_modified!r}")
    size = f", {size_bytes / 1e9:.2f} GB" if size_bytes else ""
    detail = f"newest {hours:.1f}h old{size}"
    if hours > BACKUP_CRIT_HOURS:
        return Finding("backups", "pg_dump", CRIT, detail + " — two nightly runs missed")
    if hours > BACKUP_WARN_HOURS:
        return Finding("backups", "pg_dump", WARN, detail + " — a nightly run was missed")
    return Finding("backups", "pg_dump", OK, detail)


def evaluate_health(payload: Optional[dict], error: Optional[str]) -> Finding:
    """`GET /api/health`, which the Caddyfile exempts from basic_auth.

    A `mock` processor here means the site is serving canned results: healthy,
    responsive, and not the real pipeline.
    """
    if error:
        return Finding("demo", "health", CRIT, error)
    if not payload:
        return Finding("demo", "health", INFO, "not checked")
    processor = payload.get("processor", "?")
    if processor == "mock":
        return Finding(
            "demo",
            "health",
            WARN,
            "up, but processor=mock — serving canned results, not the real pipeline",
        )
    return Finding("demo", "health", OK, f"up, processor={processor}")


def _hours_since(timestamp: Any) -> Optional[float]:
    if isinstance(timestamp, datetime):
        moment = timestamp
    else:
        try:
            moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (datetime.now(UTC) - moment).total_seconds() / 3600


# --- collection (shells out; every command is a query) -----------------------


def _run(command: list[str], timeout: int = 30) -> Optional[str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _aws_json(args: list[str]) -> Optional[dict]:
    raw = _run(["aws", *args, "--output", "json"], timeout=60)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def collect_instances(tag_names: dict[str, str]) -> dict[str, Optional[dict]]:
    found: dict[str, Optional[dict]] = {label: None for label in tag_names}
    payload = _aws_json(["ec2", "describe-instances"])
    if payload is None:
        return found
    for reservation in payload.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            if instance.get("State", {}).get("Name") == "terminated":
                continue
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            name = tags.get("Name", "")
            for label, wanted in tag_names.items():
                if wanted in name:
                    found[label] = instance
    return found


def collect_security_group(group_id: Optional[str]) -> Optional[list[dict]]:
    if not group_id:
        return None
    payload = _aws_json(["ec2", "describe-security-groups", "--group-ids", group_id])
    if not payload or not payload.get("SecurityGroups"):
        return None
    return payload["SecurityGroups"][0].get("IpPermissions", [])


def collect_backup() -> tuple[Optional[str], Optional[int]]:
    payload = _aws_json(
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            BACKUP_BUCKET,
            "--prefix",
            BACKUP_PREFIX,
        ]
    )
    if not payload or not payload.get("Contents"):
        return None, None
    newest = max(payload["Contents"], key=lambda o: o["LastModified"])
    return newest["LastModified"], newest.get("Size")


def collect_box(host: str) -> tuple[Optional[float], Optional[list[str]]]:
    """Disk free (GiB) and running janasunani containers, over SSH."""
    if shutil.which("ssh") is None:
        return None, None
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host]

    free_gib = None
    raw = _run([*ssh, "df -Pk ~ | tail -1 | awk '{print $4}'"], timeout=30)
    if raw and raw.strip().isdigit():
        free_gib = int(raw.strip()) / (1024 * 1024)

    containers = None
    raw = _run([*ssh, "docker ps --format '{{.Names}}'"], timeout=30)
    if raw is not None:
        containers = [
            line.strip() for line in raw.splitlines() if line.strip().startswith("janasunani")
        ]
    return free_gib, containers


def collect_health(site: Optional[str]) -> tuple[Optional[dict], Optional[str]]:
    if not site:
        return None, None
    url = f"https://{site}/api/health"
    raw = _run(["curl", "-fsS", "--max-time", "15", url], timeout=30)
    if raw is None:
        return None, f"{url} unreachable or non-200"
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return None, f"{url} returned non-JSON"


# --- reporting ---------------------------------------------------------------


def render(report: Report) -> str:
    marks = {OK: "OK  ", WARN: "WARN", CRIT: "CRIT", INFO: "--  "}
    lines: list[str] = []
    section = None
    for finding in report.findings:
        if finding.section != section:
            section = finding.section
            lines.append(f"\n=== {section} ===")
        lines.append(f"[{marks[finding.status]}] {finding.name}: {finding.detail}")
    lines.append("")
    counts = {
        level: sum(1 for f in report.findings if f.status == level)
        for level in (CRIT, WARN, INFO)
    }
    checked = len(report.findings) - counts[INFO]
    skipped = f" ({counts[INFO]} not checked)" if counts[INFO] else ""

    if counts[CRIT]:
        lines.append(f"{counts[CRIT]} critical, {counts[WARN]} warning{skipped}")
    elif counts[WARN]:
        lines.append(f"no critical issues, {counts[WARN]} warning{skipped}")
    elif checked == 0:
        # Must precede the partial-pass branch below: with nothing checked,
        # every finding is INFO and this would otherwise read "all clear on 0
        # checks". Unreachable AWS, an unreachable box and --no-aws --no-ssh
        # all land here, and reading that as healthy is the exact mistake this
        # tool exists to prevent.
        lines.append("NOTHING CHECKED — this is not a clean bill of health")
    elif counts[INFO]:
        lines.append(
            f"all clear on {checked} checks, but {counts[INFO]} were not run — "
            "see the '--' lines above"
        )
    else:
        lines.append(f"all clear ({checked} checks){skipped}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default="ubuntu@52.66.116.80", help="SSH target for the CPU box.")
    parser.add_argument(
        "--site", default=None, help="Public site for the health check (e.g. 52-66-116-80.nip.io)."
    )
    parser.add_argument("--sg-id", default=None, help="CPU box security group id.")
    parser.add_argument("--no-ssh", action="store_true", help="Skip the box-side checks.")
    parser.add_argument("--no-aws", action="store_true", help="Skip the AWS API checks.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = Report()

    if args.no_aws:
        report.add("compute", "aws", INFO, "skipped (--no-aws)")
    elif shutil.which("aws") is None:
        report.add("compute", "aws", INFO, "aws CLI not installed — AWS checks skipped")
    else:
        instances = collect_instances({"cpu box": "cpu", "gpu box": "gpu"})
        report.findings.append(
            evaluate_instance("cpu box", instances["cpu box"], always_on=True)
        )
        report.findings.append(
            evaluate_instance("gpu box", instances["gpu box"], always_on=False)
        )

        permissions = collect_security_group(args.sg_id)
        if permissions is None:
            report.add(
                "network",
                "ssh exposure",
                INFO,
                "not checked (pass --sg-id, from `terraform output cpu_box_security_group_id`)",
            )
        else:
            report.findings.extend(evaluate_ssh_exposure(permissions))

        last_modified, size = collect_backup()
        report.findings.append(evaluate_backup(last_modified, size))

    if args.no_ssh:
        report.add("box", "ssh", INFO, "skipped (--no-ssh)")
    else:
        free_gib, containers = collect_box(args.host)
        if free_gib is None and containers is None:
            report.add("box", "ssh", INFO, f"{args.host} unreachable — box checks skipped")
        else:
            report.findings.append(evaluate_disk(free_gib))
            report.findings.extend(evaluate_containers(containers))

    payload, error = collect_health(args.site)
    if args.site:
        report.findings.append(evaluate_health(payload, error))
    else:
        report.add("demo", "health", INFO, "not checked (pass --site)")

    if args.json:
        print(
            json.dumps(
                {
                    "worst": report.worst,
                    "findings": [vars(f) for f in report.findings],
                },
                indent=2,
            )
        )
    else:
        print(render(report))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
