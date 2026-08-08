"""Open port 22 to this machine's current IP, then SSH into the CPU box.

The security group pins SSH to a single `/32`. That is the right posture for a
box holding real citizen data, but a home or office lease moves, and when it
does SSH stops working with no ICMP and a closed port -- indistinguishable from
the instance being down unless you go and ask AWS. This closes that loop.

**What it changes and what it does not.** The only mutation is one
`authorize-security-group-ingress` for `tcp/22` from `<your ip>/32`, and only
when no existing rule already covers you. It never widens a rule, never accepts
a CIDR wider than /32, and never touches any other port or group.

Revoking is deliberately opt-in (`--prune`), because the rules it would remove
may belong to a colleague who is also working right now. `--prune` is the
answer to issue #32: the deploy workflow opens 22 to the runner's /32 and
revokes it afterwards, so a runner that dies in between leaves a rule behind,
and those accumulate silently.

Nothing here prints a secret, and the SSH key comes from the agent as usual.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import urllib.request

AWS_REGION_DEFAULT = "ap-south-1"
INSTANCE_NAME_TAG = "janasunani-cpu-box"
PROJECT_TAG_VALUE = "janasunani"
SSH_PORT = 22
IP_SERVICE = "https://checkip.amazonaws.com"


def _run_json(args: list[str], region: str) -> dict | None:
    if shutil.which("aws") is None:
        print("aws CLI not found on PATH", file=sys.stderr)
        return None
    try:
        out = subprocess.run(
            ["aws", *args, "--region", region, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"aws {' '.join(args[:2])} failed: {exc}", file=sys.stderr)
        return None
    if out.returncode != 0:
        print(f"aws {' '.join(args[:2])} failed: {out.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def current_ip() -> str | None:
    """This machine's public IPv4, from the same service the deploy job uses."""
    try:
        with urllib.request.urlopen(IP_SERVICE, timeout=15) as response:
            raw = response.read().decode("utf-8").strip()
    except Exception as exc:  # noqa: BLE001 - any failure is just "unknown"
        print(f"could not determine public IP: {exc}", file=sys.stderr)
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        print("public IP service did not return an IP address", file=sys.stderr)
        return None
    if address.version != 4:
        print(f"got an IPv6 address ({raw}); the SG rule is IPv4", file=sys.stderr)
        return None
    return str(address)


def find_box(region: str) -> dict | None:
    """The CPU box, matched on Name *and* Project so a lookalike cannot stand in."""
    payload = _run_json(["ec2", "describe-instances"], region)
    if payload is None:
        return None
    for reservation in payload.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            if instance.get("State", {}).get("Name") == "terminated":
                continue
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            if tags.get("Name") != INSTANCE_NAME_TAG:
                continue
            if tags.get("Project") != PROJECT_TAG_VALUE:
                continue
            return instance
    return None


def _covers_ssh(rule: dict) -> bool:
    """Whether an IpPermission covers tcp/22, including the wildcard forms."""
    if rule.get("IpProtocol") == "-1":
        return True
    if rule.get("IpProtocol") != "tcp":
        return False
    start, end = rule.get("FromPort"), rule.get("ToPort")
    if start is None or end is None:
        return False
    return int(start) <= SSH_PORT <= int(end)


def ssh_cidrs(permissions: list[dict]) -> set[str]:
    out: set[str] = set()
    for rule in permissions:
        if not _covers_ssh(rule):
            continue
        for entry in rule.get("IpRanges", []):
            cidr = entry.get("CidrIp")
            if cidr:
                out.add(cidr)
    return out


def _authorize(group_id: str, cidr: str, region: str) -> bool:
    print(f"opening tcp/{SSH_PORT} to {cidr} on {group_id}")
    result = subprocess.run(
        [
            "aws", "ec2", "authorize-security-group-ingress",
            "--group-id", group_id,
            "--protocol", "tcp", "--port", str(SSH_PORT),
            "--cidr", cidr,
            "--region", region,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip()
        if "InvalidPermission.Duplicate" in message:
            return True
        print(f"authorize failed: {message}", file=sys.stderr)
        return False
    return True


def _revoke(group_id: str, cidr: str, region: str) -> None:
    print(f"revoking stale tcp/{SSH_PORT} rule for {cidr}")
    subprocess.run(
        [
            "aws", "ec2", "revoke-security-group-ingress",
            "--group-id", group_id,
            "--protocol", "tcp", "--port", str(SSH_PORT),
            "--cidr", cidr,
            "--region", region,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="user@host to SSH into.")
    parser.add_argument("--region", default=AWS_REGION_DEFAULT)
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Also revoke other /32 SSH rules (issue #32 leakage). Off by default: "
        "one of them may be a colleague who is connected right now.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would change and exit without opening anything or SSHing.",
    )
    parser.add_argument("command", nargs="*", help="Optional remote command.")
    args = parser.parse_args(argv)

    ip = current_ip()
    if ip is None:
        return 1
    mine = f"{ip}/32"
    print(f"this machine: {mine}")

    box = find_box(args.region)
    if box is None:
        print(
            "could not find the CPU box (credentials, region, or it is gone). "
            "`make infra` reports on that specifically.",
            file=sys.stderr,
        )
        return 1

    group_ids = [g["GroupId"] for g in box.get("SecurityGroups", []) if g.get("GroupId")]
    if not group_ids:
        print("the CPU box has no attached security group", file=sys.stderr)
        return 1

    # Any attached group can expose 22, so check them all before concluding
    # a rule is missing and opening a new one.
    permissions_by_group: dict[str, list[dict]] = {}
    for group_id in group_ids:
        payload = _run_json(
            ["ec2", "describe-security-groups", "--group-ids", group_id], args.region
        )
        if payload is None:
            return 1
        groups = payload.get("SecurityGroups", [])
        permissions_by_group[group_id] = groups[0].get("IpPermissions", []) if groups else []

    existing = {c for perms in permissions_by_group.values() for c in ssh_cidrs(perms)}
    already_open = any(
        ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
        for cidr in existing
    )

    if already_open:
        print(f"already permitted by: {', '.join(sorted(existing))}")
    else:
        print(f"not permitted; current SSH rules: {', '.join(sorted(existing)) or 'none'}")

    if args.check:
        if not already_open:
            print(f"would open {mine}")
        if args.prune:
            stale = {c for c in existing if c.endswith("/32") and c != mine}
            for cidr in sorted(stale):
                print(f"would revoke {cidr}")
        return 0

    target_group = group_ids[0]
    if not already_open and not _authorize(target_group, mine, args.region):
        return 1

    if args.prune:
        for group_id, perms in permissions_by_group.items():
            for cidr in sorted(ssh_cidrs(perms)):
                if cidr.endswith("/32") and cidr != mine:
                    _revoke(group_id, cidr, args.region)

    ssh = ["ssh", "-A", "-o", "ConnectTimeout=20", args.host, *args.command]
    print(f"$ {' '.join(ssh)}")
    return subprocess.call(ssh, env=os.environ.copy())


if __name__ == "__main__":
    sys.exit(main())
