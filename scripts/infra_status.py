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
import ipaddress
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
# deploy/terraform/variables.tf's aws_region default, also config.py's
# Settings.AWS_REGION default. Pinned explicitly on every `aws` call rather
# than left to the caller's default CLI profile/region: a profile pointed
# elsewhere gets a successful, empty response, not an error, and that reads
# as "the box is gone" (see collect_instances).
AWS_REGION_DEFAULT = "ap-south-1"
# deploy/terraform/main.tf and gpu.tf: the exact `Name` tag each instance
# resource sets. `Project` comes from the provider's own `default_tags` block
# (main.tf), applied to every resource it manages, not just these two --
# required alongside Name so an unrelated instance in a shared account (e.g.
# a substring match on "cpu" hitting a "batch-cpu-worker") cannot stand in
# for a missing production box.
INSTANCE_NAME_TAGS = {"cpu box": "janasunani-cpu-box", "gpu box": "janasunani-gpu-box"}
PROJECT_TAG_VALUE = "janasunani"
# deploy.sh: min_free_kib="${MIN_FREE_KIB:-$((20 * 1024 * 1024))}"
DISK_CRIT_GIB = 20
DISK_WARN_GIB = 40
# nightly cadence, so one missed run is a warning and two is a failure
BACKUP_WARN_HOURS = 26
BACKUP_CRIT_HOURS = 48
# Not a target size -- just a floor low enough that only a truly empty or
# near-empty object (a 0-byte or header-only upload after a failed `pg_dump`)
# can fail it. The prod DB holds 1.37M complaints / 6.56M action-history rows
# (docs/ROADMAP.md); a real dump, even compressed, is orders of magnitude
# above this, so there is no meaningful risk of flagging a genuine backup.
BACKUP_MIN_SIZE_BYTES = 1024 * 1024  # 1 MiB
STACK_CONTAINERS = (
    "janasunani-oltp",
    "janasunani-api",
    "janasunani-frontend",
    "janasunani-proxy",
)
# docs/DEPLOY.md's documented first-time state: only the oltp container up
# (`up -d oltp`, deploy/docker-compose.yml: container_name janasunani-oltp),
# before the app images are deployed at all.
PRE_DEPLOY_CONTAINERS = frozenset({"janasunani-oltp"})
# deploy/terraform/main.tf: the cpu_box security group's standing ingress
# rule, `description = "SSH from the maintainer IP only"`. Terraform attaches
# an ingress block's `description` to that CIDR's entry in the resulting
# IpPermission, so a rule carrying this exact text is the intended permanent
# access, not a leftover deploy-runner /32 (issue #32) -- distinguished here
# so a healthy box can still render all clear.
#
# The description alone is not proof of scope: deploy/terraform/variables.tf's
# `admin_cidr` validation only rejects 0.0.0.0/0, so a `/24` (or wider) still
# passes terraform and keeps this exact description, and trusting the text
# would then bless SSH from every address in that block. evaluate_ssh_exposure
# also requires the CIDR itself to be a single host (see `_is_single_host`)
# before treating it as the intended maintainer-only rule.
MAINTAINER_SSH_DESCRIPTION = "SSH from the maintainer IP only"

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
        problem, it is an absence of one, and must not read as degraded.

        This is deliberately blind to *how many* checks ran (a cron job must
        not fail just because some checks were skipped) -- `nothing_checked`
        below is the separate signal for "no real check happened here"."""
        rank = max((_RANK[f.status] for f in self.findings), default=0)
        return {0: OK, 1: WARN, 2: CRIT}[rank]

    def exit_code(self) -> int:
        return 1 if self.worst == CRIT else 0

    @property
    def checked_count(self) -> int:
        return sum(1 for f in self.findings if f.status != INFO)

    @property
    def skipped_count(self) -> int:
        return sum(1 for f in self.findings if f.status == INFO)

    @property
    def nothing_checked(self) -> bool:
        """True iff every finding is INFO -- `--no-aws --no-ssh`, an
        unreachable box, or unreachable AWS all land here. `worst` alone
        reads `OK` in this case (by design, see above), so a machine
        consumer of `--json` needs this to avoid the exact false-all-clear
        the text renderer's `NOTHING CHECKED` line exists to prevent."""
        return self.checked_count == 0


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


def _rule_covers_ssh(rule: dict) -> bool:
    """Whether an IpPermission's protocol/port coverage includes TCP/22.

    Two ways a rule can expose 22 without literally saying `FromPort: 22`:
    `IpProtocol: "-1"` is AWS's "all protocols, all ports" wildcard (no
    meaningful FromPort/ToPort), and a real TCP rule specifies an *inclusive
    range* -- `FromPort=0, ToPort=65535` covers 22 exactly as much as
    `FromPort=22, ToPort=22` does. Missing either means a wide-open box scores
    the same as a locked-down one.
    """
    protocol = rule.get("IpProtocol")
    if protocol in ("-1", -1):
        return True
    if protocol not in ("tcp", 6, "6"):
        return False
    from_port = rule.get("FromPort")
    to_port = rule.get("ToPort")
    if from_port is None or to_port is None:
        return False
    return from_port <= 22 <= to_port


def _is_single_host(cidr: str) -> bool:
    """Whether a CIDR block is exactly one address (a `/32` for IPv4, a
    `/128` for IPv6) -- what "the maintainer IP only" actually means.
    `admin_cidr`'s terraform validation only rejects `0.0.0.0/0`, so this
    cannot be assumed from the variable's description or its name.
    """
    try:
        return ipaddress.ip_network(cidr, strict=False).num_addresses == 1
    except ValueError:
        return False


def evaluate_ssh_exposure(permissions: list[dict]) -> list[Finding]:
    """Port 22 ingress. See issue #32.

    Checks both `IpRanges` (IPv4) and `Ipv6Ranges` (IPv6) — an IPv6-only
    source is exposure this tool would otherwise never see. The standing
    maintainer rule (`MAINTAINER_SSH_DESCRIPTION`) is recognized as intended
    access, not a leftover; anything else is either a deploy authorizing 22 to
    the runner's /32 (expected only for the duration of that deploy, revoked
    after — a leftover means a runner died mid-deploy) or, open to the world,
    something badly wrong.
    """
    findings: list[Finding] = []
    for rule in permissions:
        if not _rule_covers_ssh(rule):
            continue
        sources = [
            (cidr.get("CidrIp", "?"), cidr.get("Description"))
            for cidr in rule.get("IpRanges", [])
        ] + [
            (cidr.get("CidrIpv6", "?"), cidr.get("Description"))
            for cidr in rule.get("Ipv6Ranges", [])
        ]
        for block, description in sources:
            if block in ("0.0.0.0/0", "::/0"):
                findings.append(
                    Finding(
                        "network",
                        "ssh exposure",
                        CRIT,
                        f"port 22 open to {block} — the box holds production PII",
                    )
                )
            elif description == MAINTAINER_SSH_DESCRIPTION and _is_single_host(block):
                findings.append(
                    Finding(
                        "network",
                        "ssh exposure",
                        OK,
                        f"port 22 open to {block} ({description}) — standing maintainer access",
                    )
                )
            elif description == MAINTAINER_SSH_DESCRIPTION:
                # Carries the admin rule's description but is not a single
                # host -- admin_cidr's terraform validation only rejects
                # 0.0.0.0/0, so this is not hypothetical. Trusting the label
                # here would bless SSH from the whole block.
                findings.append(
                    Finding(
                        "network",
                        "ssh exposure",
                        WARN,
                        f"port 22 open to {block} ({description}) — labeled as the "
                        "maintainer rule but not a single host; check admin_cidr in "
                        "deploy/terraform",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "network",
                        "ssh exposure",
                        WARN,
                        f"port 22 open to {block}"
                        f"{f' ({description})' if description else ''}"
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


def evaluate_containers(
    running: Optional[list[str]],
    unhealthy: Optional[frozenset[str]] = None,
    starting: Optional[frozenset[str]] = None,
    all_seen: Optional[list[str]] = None,
) -> list[Finding]:
    """``unhealthy`` are containers Docker itself has marked failing their
    HEALTHCHECK (oltp/api/frontend all define one) despite still running --
    distinct from ``running`` not containing the name at all (stopped).
    ``starting`` are containers still inside their HEALTHCHECK's
    ``start_period`` (Docker's `(health: starting)`, e.g. the api's
    multi-minute model warm-up) -- not yet *confirmed* healthy, so `make
    infra` running mid-warm-up must not call that OK either, even though it
    is an expected, transient state rather than a failure.

    ``all_seen`` is every janasunani container `docker ps -a` sees,
    regardless of state -- collected so an *empty* ``running`` can be told
    apart from "never deployed": if every previously-deployed container has
    exited, `running` is empty exactly the same as a box that was never
    deployed at all, and reading that as "not deployed yet" would mask a
    full outage as a non-event. Only when `all_seen` is *also* empty is
    nothing running the pre-deploy state.
    """
    if running is None:
        return [Finding("box", "stack", INFO, "not checked")]
    if not running:
        if not all_seen:
            return [
                Finding(
                    "box",
                    "stack",
                    INFO,
                    "no janasunani containers — stack not deployed yet",
                )
            ]
        # Containers exist (docker ps -a saw them) but none are running --
        # a full outage, not a pre-deploy box. Falls through to the
        # per-container loop below, which CRITs every STACK_CONTAINERS
        # member exactly because none of them are in the (empty) `running`.
    elif set(running) == PRE_DEPLOY_CONTAINERS:
        # docs/DEPLOY.md's documented first-time bring-up: oltp only, up
        # before the app images are ever deployed. Scoring that against the
        # full STACK_CONTAINERS list below would CRIT three containers
        # nothing has tried to start yet, on every fresh box.
        return [
            Finding(
                "box",
                "stack",
                INFO,
                "only oltp up — app stack (api/frontend/proxy) not deployed yet",
            )
        ]
    unhealthy = unhealthy or frozenset()
    starting = starting or frozenset()
    findings = []
    for name in STACK_CONTAINERS:
        if name not in running:
            findings.append(Finding("box", name, CRIT, "not running"))
        elif name in unhealthy:
            findings.append(
                Finding("box", name, CRIT, "running but failing its HEALTHCHECK")
            )
        elif name in starting:
            findings.append(
                Finding("box", name, WARN, "running, HEALTHCHECK still starting (warm-up)")
            )
        else:
            findings.append(Finding("box", name, OK, "running"))
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
    size = f", {size_bytes / 1e9:.2f} GB" if size_bytes is not None else ""
    detail = f"newest {hours:.1f}h old{size}"
    if size_bytes is not None and size_bytes < BACKUP_MIN_SIZE_BYTES:
        # Fresh but empty is worse than stale: a 0-byte object after a failed
        # dump-and-upload masks the last *good* backup and would otherwise
        # read as healthy on freshness alone.
        return Finding(
            "backups",
            "pg_dump",
            CRIT,
            detail + f" — under {BACKUP_MIN_SIZE_BYTES:,} bytes, looks like a failed "
            "dump, not a restorable backup",
        )
    if hours > BACKUP_CRIT_HOURS:
        return Finding("backups", "pg_dump", CRIT, detail + " — two nightly runs missed")
    if hours > BACKUP_WARN_HOURS:
        return Finding("backups", "pg_dump", WARN, detail + " — a nightly run was missed")
    return Finding("backups", "pg_dump", OK, detail)


def evaluate_health(payload: Optional[dict], error: Optional[str]) -> Finding:
    """`GET /api/health`, which the Caddyfile exempts from basic_auth.

    Only `processor="pipeline"` (`PipelineGrievanceProcessor.name`) is OK --
    matching `deploy.sh`'s own smoke gate, which greps for that exact string
    and would still be waiting on anything else. `mock`
    (`MockGrievanceProcessor.name`) is the one other value the codebase can
    actually produce: healthy and responsive, but serving canned results.
    Anything besides those two -- missing, malformed, or a value neither
    processor uses -- is not something the deploy would ever consider ready,
    so it is not OK either.
    """
    if error:
        return Finding("demo", "health", CRIT, error)
    if not payload:
        return Finding("demo", "health", INFO, "not checked")
    processor = payload.get("processor", "?")
    if processor == "pipeline":
        return Finding("demo", "health", OK, f"up, processor={processor}")
    if processor == "mock":
        return Finding(
            "demo",
            "health",
            WARN,
            "up, but processor=mock — serving canned results, not the real pipeline",
        )
    return Finding(
        "demo",
        "health",
        WARN,
        f"up, but processor={processor!r} — not the expected pipeline "
        "(deploy.sh's own smoke gate would still be waiting on this)",
    )


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


def _aws_json(args: list[str], region: str) -> Optional[dict]:
    # Pinned rather than left to the caller's default profile/region
    # (AWS_REGION_DEFAULT below): an operator whose default profile points
    # elsewhere gets a *successful, empty* describe-instances response, which
    # is indistinguishable from the box actually being gone.
    raw = _run(["aws", *args, "--region", region, "--output", "json"], timeout=60)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def collect_instances(
    tag_names: dict[str, str], region: str
) -> Optional[dict[str, Optional[dict]]]:
    """`tag_names` maps a report label to the exact `Name` tag to match (see
    `INSTANCE_NAME_TAGS`) -- an instance must also carry
    `Project=PROJECT_TAG_VALUE` to qualify, so an unrelated instance sharing
    part of the name (or the name outright, in a shared account) cannot stand
    in for a missing production box. An exact match also means at most one
    instance can satisfy a given label, so there is no "later matches
    overwrite earlier ones" ambiguity left to depend on response order for.

    None means the `describe-instances` call itself failed (credentials,
    region, IAM, network) -- distinct from a successful call that simply found
    no matching instance. Conflating the two turns "AWS is unreachable" into
    "the box is gone", which is a false production alarm, not a status check.
    """
    payload = _aws_json(["ec2", "describe-instances"], region)
    if payload is None:
        return None
    found: dict[str, Optional[dict]] = {label: None for label in tag_names}
    for reservation in payload.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            if instance.get("State", {}).get("Name") == "terminated":
                continue
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            if tags.get("Project") != PROJECT_TAG_VALUE:
                continue
            name = tags.get("Name", "")
            for label, wanted in tag_names.items():
                if name == wanted:
                    found[label] = instance
    return found


def collect_security_group(group_id: Optional[str], region: str) -> Optional[list[dict]]:
    if not group_id:
        return None
    payload = _aws_json(["ec2", "describe-security-groups", "--group-ids", group_id], region)
    if not payload or not payload.get("SecurityGroups"):
        return None
    return payload["SecurityGroups"][0].get("IpPermissions", [])


def collect_backup(region: str) -> Optional[tuple[Optional[str], Optional[int]]]:
    """Outer None means `list-objects-v2` itself failed; ``(None, None)``
    means it succeeded and found nothing under the backup prefix -- the two
    read very differently (see `collect_instances`)."""
    payload = _aws_json(
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            BACKUP_BUCKET,
            "--prefix",
            BACKUP_PREFIX,
        ],
        region,
    )
    if payload is None:
        return None
    if not payload.get("Contents"):
        return None, None
    newest = max(payload["Contents"], key=lambda o: o["LastModified"])
    return newest["LastModified"], newest.get("Size")


def collect_box(host: str) -> tuple[
    Optional[float],
    Optional[list[str]],
    Optional[frozenset[str]],
    Optional[frozenset[str]],
    Optional[list[str]],
]:
    """Disk free (GiB), running janasunani containers, which of those Docker
    considers unhealthy, which are still inside their HEALTHCHECK's
    start_period, and every janasunani container that exists regardless of
    state, over SSH.

    Uses `docker ps -a`, not plain `docker ps`: without `-a`, an exited
    container is indistinguishable from one that was never created at all --
    both are simply absent from the output -- so a box where the whole stack
    has crashed would read exactly like a box nothing has ever been deployed
    to. `-a`'s Status field always starts with "Up" for a running container
    (e.g. "Up 5 minutes (healthy)") and something else for anything not
    running ("Exited (1) 2 hours ago", "Created", ...), which is what
    separates `running` from the last return value below.
    """
    if shutil.which("ssh") is None:
        return None, None, None, None, None
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host]

    free_gib = None
    raw = _run([*ssh, "df -Pk ~ | tail -1 | awk '{print $4}'"], timeout=30)
    if raw and raw.strip().isdigit():
        free_gib = int(raw.strip()) / (1024 * 1024)

    running = None
    unhealthy = None
    starting = None
    all_seen = None
    raw = _run([*ssh, "docker ps -a --format '{{.Names}}\t{{.Status}}'"], timeout=30)
    if raw is not None:
        running = []
        all_seen = []
        unhealthy_set = set()
        starting_set = set()
        for line in raw.splitlines():
            name, _, status = line.strip().partition("\t")
            if not name.startswith("janasunani"):
                continue
            all_seen.append(name)
            if not status.startswith("Up"):
                continue
            running.append(name)
            if "(unhealthy)" in status:
                unhealthy_set.add(name)
            elif "(health: starting)" in status:
                starting_set.add(name)
        unhealthy = frozenset(unhealthy_set)
        starting = frozenset(starting_set)
    return free_gib, running, unhealthy, starting, all_seen


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
    checked = report.checked_count
    skipped = f" ({counts[INFO]} not checked)" if counts[INFO] else ""

    if counts[CRIT]:
        lines.append(f"{counts[CRIT]} critical, {counts[WARN]} warning{skipped}")
    elif counts[WARN]:
        lines.append(f"no critical issues, {counts[WARN]} warning{skipped}")
    elif report.nothing_checked:
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
    parser.add_argument(
        "--region",
        default=AWS_REGION_DEFAULT,
        help=f"AWS region for every query (default: {AWS_REGION_DEFAULT}, matching "
        "deploy/terraform).",
    )
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
        instances = collect_instances(INSTANCE_NAME_TAGS, args.region)
        if instances is None:
            report.add(
                "compute",
                "aws",
                INFO,
                f"describe-instances failed (region={args.region}; check credentials/IAM/"
                "network) — not checked",
            )
        else:
            report.findings.append(
                evaluate_instance("cpu box", instances["cpu box"], always_on=True)
            )
            report.findings.append(
                evaluate_instance("gpu box", instances["gpu box"], always_on=False)
            )

        if not args.sg_id:
            report.add(
                "network",
                "ssh exposure",
                INFO,
                "not checked (pass --sg-id, from `terraform output cpu_box_security_group_id`)",
            )
        else:
            permissions = collect_security_group(args.sg_id, args.region)
            if permissions is None:
                report.add(
                    "network",
                    "ssh exposure",
                    INFO,
                    f"describe-security-groups for {args.sg_id} failed (region={args.region}) "
                    "— not checked",
                )
            else:
                report.findings.extend(evaluate_ssh_exposure(permissions))

        backup = collect_backup(args.region)
        if backup is None:
            report.add(
                "backups",
                "pg_dump",
                INFO,
                f"list-objects-v2 failed (region={args.region}; check credentials/network) "
                "— not checked",
            )
        else:
            last_modified, size = backup
            report.findings.append(evaluate_backup(last_modified, size))

    if args.no_ssh:
        report.add("box", "ssh", INFO, "skipped (--no-ssh)")
    else:
        free_gib, containers, unhealthy, starting, all_seen = collect_box(args.host)
        if free_gib is None and containers is None:
            report.add("box", "ssh", INFO, f"{args.host} unreachable — box checks skipped")
        else:
            report.findings.append(evaluate_disk(free_gib))
            report.findings.extend(
                evaluate_containers(containers, unhealthy, starting, all_seen)
            )

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
                    # `worst` alone can read "OK" when nothing was actually
                    # checked (--no-aws --no-ssh, unreachable AWS/box) --
                    # deliberately, see Report.worst -- so a machine consumer
                    # needs this to not treat a fully-skipped run as healthy.
                    "nothing_checked": report.nothing_checked,
                    "checked": report.checked_count,
                    "skipped": report.skipped_count,
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
