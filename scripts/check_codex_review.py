"""Gate a pull request on the Codex review having run and been answered.

Codex in GitHub reports neither a check run nor a commit status, so there is
nothing to mark required in branch protection. It leaves two signals instead:

- If it has findings, it posts a review whose body carries
  ``**Reviewed commit:** `<sha>` `` and one inline thread per finding.
- If it is clean, it posts nothing at all and reacts :+1:.

This script reads both and decides whether the pull request satisfies the
review protocol in CONTRIBUTING.md: Codex has looked at the current head, and
no finding is left unanswered. Threads are the unit of "answered" because that
is what the protocol already tracks -- fixed, filed as an issue, or rejected
with evidence, then resolved.

The clean signal carries no commit sha, so freshness for that path is decided
on time: the reaction must be newer than the head commit. A force-push of an
older commit can therefore keep a stale :+1: valid; pushing new work cannot.

Stdlib only, so the workflow runs it without installing the project.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

API_ROOT = "https://api.github.com"

# REST reports the login with the suffix, GraphQL without it.
CODEX_LOGINS = {"chatgpt-codex-connector", "chatgpt-codex-connector[bot]"}

REVIEWED_COMMIT_RE = re.compile(r"Reviewed commit:\*\*\s*`([0-9a-f]{7,40})`")

# CONTRIBUTING.md exempts "small docs-only or config-only branches". Docs-only
# is decidable from the diff; config-only is not, so it goes through the label.
# "Small" is load-bearing and not defined upstream: a docs rewrite large enough
# to restate a policy deserves the review, so past this many changed lines the
# exemption lapses and the branch goes through the normal gate or the label.
DOCS_ONLY_PATTERNS = ("*.md", "docs/*", "docs/**", "*.rst", "LICENSE")
DOCS_ONLY_MAX_LINES = 400
SKIP_LABEL = "codex-review-not-required"

RestFetch = Callable[[str], Any]
GraphQLFetch = Callable[[str, dict[str, Any]], Any]


@dataclass(frozen=True)
class Verdict:
    """Outcome of the gate, shaped for a GitHub check run."""

    conclusion: str  # "success" | "failure"
    title: str
    summary: str

    @property
    def ok(self) -> bool:
        return self.conclusion == "success"


class GitHubError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


def _request(url: str, token: str, method: str = "GET", body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        raise GitHubError(f"{method} {url} -> {exc.code}: {exc.read().decode()}") from exc
    return json.loads(payload) if payload else None


def make_rest_fetch(repo: str, token: str) -> RestFetch:
    """Return a paginating GET for repo-relative REST paths."""

    def fetch(path: str) -> Any:
        url = f"{API_ROOT}/repos/{repo}/{path.lstrip('/')}"
        first = _request(_with_per_page(url), token)
        if not isinstance(first, list):
            return first
        items = list(first)
        page = 2
        while len(first) == 100 and page <= 10:
            more = _request(_with_per_page(url, page=page), token)
            if not more:
                break
            items.extend(more)
            if len(more) < 100:
                break
            page += 1
        return items

    return fetch


def _with_per_page(url: str, page: int | None = None) -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["per_page"] = "100"
    if page is not None:
        query["page"] = str(page)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def make_graphql_fetch(token: str) -> GraphQLFetch:
    def fetch(query: str, variables: dict[str, Any]) -> Any:
        payload = _request(
            f"{API_ROOT}/graphql",
            token,
            method="POST",
            body={"query": query, "variables": variables},
        )
        if payload and payload.get("errors"):
            raise GitHubError(f"GraphQL errors: {payload['errors']}")
        return payload
    return fetch


# --------------------------------------------------------------------------
# Signal extraction
# --------------------------------------------------------------------------


def is_codex(login: str | None) -> bool:
    return (login or "").lower() in CODEX_LOGINS


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def reviewed_shas(reviews: Iterable[dict[str, Any]]) -> list[str]:
    """Shas Codex says it reviewed, in the order the reviews were posted."""
    found: list[str] = []
    for review in reviews:
        if not is_codex((review.get("user") or {}).get("login")):
            continue
        match = REVIEWED_COMMIT_RE.search(review.get("body") or "")
        if match:
            found.append(match.group(1))
    return found


def latest_thumbs_up(rest: RestFetch, pr_number: int) -> datetime | None:
    """When Codex last signalled "clean" on the PR body or a comment."""
    targets = [f"issues/{pr_number}/reactions"]
    for comment in rest(f"issues/{pr_number}/comments") or []:
        if (comment.get("reactions") or {}).get("total_count"):
            targets.append(f"issues/comments/{comment['id']}/reactions")

    newest: datetime | None = None
    for path in targets:
        for reaction in rest(path) or []:
            if reaction.get("content") != "+1":
                continue
            if not is_codex((reaction.get("user") or {}).get("login")):
                continue
            stamp = parse_timestamp(reaction["created_at"])
            if newest is None or stamp > newest:
                newest = stamp
    return newest


def unresolved_codex_threads(
    graphql: GraphQLFetch, owner: str, name: str, pr_number: int
) -> list[str]:
    """First-line summaries of Codex threads nobody has resolved."""
    query = """
    query($owner:String!, $name:String!, $number:Int!, $cursor:String) {
      repository(owner:$owner, name:$name) {
        pullRequest(number:$number) {
          reviewThreads(first:100, after:$cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              isResolved
              comments(first:1) { nodes { author { login } body path } }
            }
          }
        }
      }
    }
    """
    unresolved: list[str] = []
    cursor: str | None = None
    while True:
        payload = graphql(
            query, {"owner": owner, "name": name, "number": pr_number, "cursor": cursor}
        )
        threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
        for thread in threads["nodes"]:
            if thread["isResolved"]:
                continue
            comments = (thread.get("comments") or {}).get("nodes") or []
            if not comments:
                continue
            first = comments[0]
            if not is_codex((first.get("author") or {}).get("login")):
                continue
            unresolved.append(_thread_label(first))
        if not threads["pageInfo"]["hasNextPage"]:
            return unresolved
        cursor = threads["pageInfo"]["endCursor"]


def _thread_label(comment: dict[str, Any]) -> str:
    """Codex titles a finding in bold on the first non-badge line."""
    path = comment.get("path") or "?"
    for line in (comment.get("body") or "").splitlines():
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        text = re.sub(r"</?(sub|sup|b|strong)>", "", text)
        text = text.replace("*", "").strip()
        if text:
            return f"{path}: {text}"
    return path


def is_docs_only(files: Sequence[dict[str, Any]]) -> bool:
    if not files:
        return False
    return all(
        any(fnmatch.fnmatch(entry["filename"], pattern) for pattern in DOCS_ONLY_PATTERNS)
        for entry in files
    )


def changed_lines(files: Iterable[dict[str, Any]]) -> int:
    return sum(entry.get("additions", 0) + entry.get("deletions", 0) for entry in files)


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------


def evaluate(rest: RestFetch, graphql: GraphQLFetch, repo: str, pr_number: int) -> Verdict:
    owner, name = repo.split("/", 1)
    pull = rest(f"pulls/{pr_number}")
    head_sha = pull["head"]["sha"]
    labels = {label["name"] for label in pull.get("labels") or []}

    if SKIP_LABEL in labels:
        return Verdict("success", "Codex review not required", f"Skipped by `{SKIP_LABEL}`.")

    files = rest(f"pulls/{pr_number}/files") or []
    if is_docs_only(files) and changed_lines(files) <= DOCS_ONLY_MAX_LINES:
        return Verdict(
            "success",
            "Codex review not required",
            f"Docs-only branch, {changed_lines(files)} changed lines; "
            "CONTRIBUTING.md exempts small ones.",
        )

    if pull.get("draft"):
        return Verdict("success", "Draft", "Codex reviews on ready-for-review.")

    reviews = rest(f"pulls/{pr_number}/reviews") or []
    reviewed = reviewed_shas(reviews)
    reviewed_head = any(head_sha.startswith(sha) for sha in reviewed)

    thumbs_up = latest_thumbs_up(rest, pr_number)
    head_commit = rest(f"commits/{head_sha}")
    head_time = parse_timestamp(head_commit["commit"]["committer"]["date"])
    cleared_head = thumbs_up is not None and thumbs_up > head_time

    if not reviewed_head and not cleared_head:
        return Verdict(
            "failure",
            f"Codex has not reviewed {head_sha[:10]}",
            _stale_summary(head_sha, reviewed, thumbs_up, head_time),
        )

    unresolved = unresolved_codex_threads(graphql, owner, name, pr_number)
    if unresolved:
        listed = "\n".join(f"- {item}" for item in unresolved)
        return Verdict(
            "failure",
            f"{len(unresolved)} Codex finding(s) unanswered",
            "CONTRIBUTING.md: every finding ends fixed, filed as an issue, or "
            "rejected in a reply that shows the evidence. Reply, then resolve "
            f"the thread.\n\n{listed}",
        )

    how = "reviewed with findings, all answered" if reviewed_head else "cleared with :+1:"
    return Verdict("success", "Codex review passed", f"Head `{head_sha[:10]}` {how}.")


def _stale_summary(
    head_sha: str,
    reviewed: Sequence[str],
    thumbs_up: datetime | None,
    head_time: datetime,
) -> str:
    lines = [f"Comment `@codex review` on the pull request. Head is `{head_sha[:10]}`."]
    if reviewed:
        lines.append(f"Codex last reviewed `{reviewed[-1]}`.")
    if thumbs_up is not None:
        lines.append(
            f"The :+1: at {thumbs_up:%Y-%m-%d %H:%M UTC} predates the head "
            f"commit at {head_time:%Y-%m-%d %H:%M UTC}."
        )
    elif not reviewed:
        lines.append("Codex has left no review or reaction on this pull request.")
    return " ".join(lines)


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def post_check_run(repo: str, token: str, head_sha: str, name: str, verdict: Verdict) -> None:
    _request(
        f"{API_ROOT}/repos/{repo}/check-runs",
        token,
        method="POST",
        body={
            "name": name,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": verdict.conclusion,
            "output": {"title": verdict.title, "summary": verdict.summary},
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--check-name", default="codex-review")
    parser.add_argument(
        "--post-check-run",
        action="store_true",
        help="Report the verdict as a check run on the PR head instead of exiting non-zero.",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    if not args.repo:
        print("--repo or GITHUB_REPOSITORY is required", file=sys.stderr)
        return 2

    rest = make_rest_fetch(args.repo, token)
    graphql = make_graphql_fetch(token)
    verdict = evaluate(rest, graphql, args.repo, args.pr)

    print(f"{verdict.conclusion}: {verdict.title}\n{verdict.summary}")

    if args.post_check_run:
        head_sha = rest(f"pulls/{args.pr}")["head"]["sha"]
        post_check_run(args.repo, token, head_sha, args.check_name, verdict)
        return 0
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
