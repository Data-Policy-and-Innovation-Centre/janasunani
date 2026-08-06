"""Fail when a DVC stage's declared deps change without a dvc.lock update.

Parses dvc.yaml deps and checks git diff against origin/main. Cheap shell
check that would have caught f890120 without needing data or the DVC remote.

Stdlib only — runs on a bare runner before deps are installed.

Usage:
  python3 scripts/check_dvc_lock.py
  python3 scripts/check_dvc_lock.py --base origin/main --head HEAD
  git diff --name-only origin/main...HEAD | python3 scripts/check_dvc_lock.py --stdin

Exits 0 when either no dep was touched or dvc.lock was updated alongside it,
1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Fallback: if PyYAML is unavailable, parse dvc.yaml with a tiny regex.
# This keeps the script stdlib-only on the bare runner.

def _parse_deps_via_yaml(path: Path) -> list[str]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text())
        deps: list[str] = []
        for stage in data.get("stages", {}).values():
            for dep in stage.get("deps", []):
                # deps can be str or {"path": str}
                if isinstance(dep, str):
                    deps.append(dep)
                elif isinstance(dep, dict) and "path" in dep:
                    deps.append(dep["path"])
        return deps
    except Exception:
        return _parse_deps_via_regex(path)


def _parse_deps_via_regex(path: Path) -> list[str]:
    text = path.read_text()
    # Find each "- <path>" under a "deps:" block. Good enough for dvc.yaml's
    # flat stage structure.
    deps: list[str] = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"deps:\s*$", stripped):
            in_deps = True
            continue
        if in_deps:
            m = re.match(r"-\s+([^\s#]+)", stripped)
            if m:
                deps.append(m.group(1).strip())
                continue
            # Deps block ends at next top-level key (outs:, cmd:, etc.) or blank
            if re.match(r"(outs|cmd|desc|params|metrics|plots|wdir):", stripped):
                in_deps = False
            elif stripped == "" or stripped.startswith("#"):
                continue
            elif not stripped.startswith("-"):
                # likely next stage or outs
                if "outs:" in stripped or "stages:" in stripped:
                    in_deps = False
    return deps


def _changed_files(base: str, head: str) -> list[str]:
    # Try git diff base...head, fall back to base..head
    for sep in ["...", ".."]:
        try:
            out = subprocess.check_output(
                ["git", "diff", "--name-only", f"{base}{sep}{head}"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            files = [f.strip() for f in out.splitlines() if f.strip()]
            if files or sep == "..":
                return files
        except subprocess.CalledProcessError:
            continue
    return []


def _is_dep_match(changed: str, dep: str) -> bool:
    # Exact file match or directory prefix match. A dep that is a directory
    # (no file extension or ends with /) matches any file under it.
    # We treat both "janasunani/pipeline/stages/format_classifier" and
    # "janasunani/pipeline/stages/format_classifier/" as directory prefixes.
    if changed == dep:
        return True
    # Normalize: dep without trailing slash
    dep_norm = dep.rstrip("/")
    # If changed is under dep directory
    if changed == dep_norm or changed.startswith(dep_norm + "/"):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Check dvc.lock updated with deps")
    parser.add_argument("--base", default=None, help="git base ref (default: origin/main or GITHUB_BASE_SHA)")
    parser.add_argument("--head", default="HEAD", help="git head ref")
    parser.add_argument("--stdin", action="store_true", help="read changed files from stdin instead of git")
    parser.add_argument("--dvc-yaml", default="dvc.yaml")
    parser.add_argument("--dvc-lock", default="dvc.lock")
    args = parser.parse_args()

    dvc_yaml = Path(args.dvc_yaml)
    if not dvc_yaml.exists():
        print(f"{dvc_yaml} not found, skipping", file=sys.stderr)
        return 0

    deps = _parse_deps_via_yaml(dvc_yaml)
    # Filter to janasunani/pipeline and other file deps that are code;
    # data/raw and models are also deps but we care about pipeline code.
    # We check all deps — if any dep file changed without lock, fail.

    if args.stdin:
        changed = [line.strip() for line in sys.stdin if line.strip()]
    else:
        base = args.base
        if not base:
            # In GitHub Actions PR, GITHUB_BASE_SHA is the merge base
            base = os.environ.get("GITHUB_BASE_SHA") or "origin/main"
            # If origin/main doesn't exist locally, try main
            try:
                subprocess.check_output(["git", "rev-parse", "--verify", base], stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                base = "main"
                try:
                    subprocess.check_output(["git", "rev-parse", "--verify", base], stderr=subprocess.DEVNULL)
                except subprocess.CalledProcessError:
                    base = "HEAD~1"
        changed = _changed_files(base, args.head)

    if not changed:
        return 0

    # dvc.lock must be in changed if any dep is
    lock_changed = any(c == args.dvc_lock for c in changed)

    # Find which changed files are deps
    matched: list[str] = []
    for c in changed:
        for dep in deps:
            if _is_dep_match(c, dep):
                matched.append(c)
                break

    if matched and not lock_changed:
        print("Error: the following DVC deps changed but dvc.lock was not updated:", file=sys.stderr)
        for m in sorted(set(matched)):
            print(f"  - {m}", file=sys.stderr)
        print(f"\nDeps are declared in {args.dvc_yaml}.", file=sys.stderr)
        print(f"Run: uv run --extra pipeline-core dvc repro <stage> && dvc push && git add {args.dvc_lock}", file=sys.stderr)
        print("Or if the change intentionally does not affect the output, update dvc.yaml to narrow the dep.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
