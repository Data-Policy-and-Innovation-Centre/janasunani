"""Tests for scripts/check_codex_review.py.

Fake REST/GraphQL payloads shaped after real responses from this repo's pull
requests: the review body Codex posts when it has findings, the :+1: it leaves
when it does not, and the thread resolution state the protocol turns on.

Loaded via importlib (scripts/ is not a package).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("check_codex_review")

REPO = "Data-Policy-and-Innovation-Centre/janasunani"
HEAD = "f319c879a1b2c3d4e5f60718293a4b5c6d7e8f90"
HEAD_TIME = "2026-08-08T12:00:00Z"
CODEX = {"login": "chatgpt-codex-connector[bot]"}
HUMAN = {"login": "ymohanty"}

# Abridged from the real body; the parser only needs the commit line.
CODEX_REVIEW_BODY = """
### 💡 Codex Review

Here are some automated review suggestions for this pull request.

**Reviewed commit:** `{sha}`

<details> <summary>ℹ️ About Codex in GitHub</summary>
If Codex has suggestions, it will comment; otherwise it will react with 👍.
</details>
"""

FINDING_BODY = (
    "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)"
    "</sub></sub>  Exclude failed Sarvam calls from the paired scorecard**\n\n"
    "Whenever a page fails, the scorecard still counts it."
)


class FakeGitHub:
    """Minimal stand-in for the REST + GraphQL surfaces the gate reads."""

    def __init__(
        self,
        *,
        head_sha: str = HEAD,
        head_time: str = HEAD_TIME,
        draft: bool = False,
        labels: list[str] | None = None,
        files: list[str] | None = None,
        changed_lines: int = 20,
        reviews: list[dict[str, Any]] | None = None,
        comments: list[dict[str, Any]] | None = None,
        reactions: dict[str, list[dict[str, Any]]] | None = None,
        threads: list[dict[str, Any]] | None = None,
    ) -> None:
        self.head_sha = head_sha
        self.head_time = head_time
        self.draft = draft
        self.labels = labels or []
        self.files = files if files is not None else ["janasunani/evaluation/sarvam.py"]
        self.changed_lines = changed_lines
        self.reviews = reviews or []
        self.comments = comments or []
        self.reactions = reactions or {}
        self.threads = threads or []

    def rest(self, path: str) -> Any:
        if path.startswith("pulls/") and path.endswith("/files"):
            per_file, extra = divmod(self.changed_lines, len(self.files))
            return [
                {"filename": name, "additions": per_file + (extra if i == 0 else 0)}
                for i, name in enumerate(self.files)
            ]
        if path.startswith("pulls/") and path.endswith("/reviews"):
            return self.reviews
        if path.startswith("pulls/"):
            return {
                "head": {"sha": self.head_sha},
                "draft": self.draft,
                "labels": [{"name": name} for name in self.labels],
            }
        if path.startswith("commits/"):
            return {"commit": {"committer": {"date": self.head_time}}}
        if path.endswith("/comments"):
            return self.comments
        if path.endswith("/reactions"):
            return self.reactions.get(path, [])
        raise AssertionError(f"unexpected REST path: {path}")

    def graphql(self, query: str, variables: dict[str, Any]) -> Any:
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": self.threads,
                        }
                    }
                }
            }
        }

    def evaluate(self):
        return check.evaluate(self.rest, self.graphql, REPO, 207)


def codex_review(sha: str) -> dict[str, Any]:
    return {"user": CODEX, "body": CODEX_REVIEW_BODY.format(sha=sha), "state": "COMMENTED"}


def thread(*, resolved: bool, author: dict[str, str] = CODEX, body: str = FINDING_BODY):
    return {
        "isResolved": resolved,
        "comments": {
            "nodes": [
                {
                    "author": {"login": author["login"].removesuffix("[bot]")},
                    "body": body,
                    "path": "janasunani/evaluation/sarvam.py",
                }
            ]
        },
    }


def thumbs_up(at: str, *, user: dict[str, str] = CODEX) -> dict[str, Any]:
    return {"content": "+1", "user": user, "created_at": at}


# --------------------------------------------------------------------------
# The clean path: Codex reacts and posts nothing
# --------------------------------------------------------------------------


def test_thumbs_up_after_head_commit_passes():
    api = FakeGitHub(
        comments=[{"id": 1, "reactions": {"total_count": 1}}],
        reactions={"issues/comments/1/reactions": [thumbs_up("2026-08-08T12:05:00Z")]},
    )
    verdict = api.evaluate()
    assert verdict.ok
    assert "cleared" in verdict.summary


def test_thumbs_up_on_pull_request_body_passes():
    api = FakeGitHub(reactions={"issues/207/reactions": [thumbs_up("2026-08-08T12:05:00Z")]})
    assert api.evaluate().ok


def test_thumbs_up_predating_head_commit_fails():
    """A push after the clean run invalidates it; the reaction carries no sha."""
    api = FakeGitHub(
        comments=[{"id": 1, "reactions": {"total_count": 1}}],
        reactions={"issues/comments/1/reactions": [thumbs_up("2026-08-08T11:00:00Z")]},
    )
    verdict = api.evaluate()
    assert not verdict.ok
    assert "predates the head commit" in verdict.summary


def test_thumbs_up_from_a_human_is_not_the_signal():
    api = FakeGitHub(
        comments=[{"id": 1, "reactions": {"total_count": 1}}],
        reactions={
            "issues/comments/1/reactions": [thumbs_up("2026-08-08T12:05:00Z", user=HUMAN)]
        },
    )
    assert not api.evaluate().ok


def test_comments_without_reactions_are_not_fetched():
    """total_count == 0 must not cost a request; the sweep runs every 10 minutes."""
    api = FakeGitHub(comments=[{"id": 9, "reactions": {"total_count": 0}}])
    api.reactions = {}  # a fetch for comment 9 would raise in rest()
    assert not api.evaluate().ok


# --------------------------------------------------------------------------
# The findings path: Codex reviews, threads must be answered
# --------------------------------------------------------------------------


def test_review_of_head_with_all_threads_resolved_passes():
    api = FakeGitHub(
        reviews=[codex_review(HEAD[:10])],
        threads=[thread(resolved=True), thread(resolved=True)],
    )
    verdict = api.evaluate()
    assert verdict.ok
    assert "all answered" in verdict.summary


def test_unresolved_codex_thread_fails_and_names_the_finding():
    api = FakeGitHub(reviews=[codex_review(HEAD[:10])], threads=[thread(resolved=False)])
    verdict = api.evaluate()
    assert not verdict.ok
    assert "1 Codex finding(s) unanswered" in verdict.title
    assert "Exclude failed Sarvam calls from the paired scorecard" in verdict.summary
    assert "janasunani/evaluation/sarvam.py" in verdict.summary


def test_unresolved_human_thread_does_not_fail_the_gate():
    """Reviewer notes on our own PRs stay open by habit; the gate is about Codex."""
    api = FakeGitHub(
        reviews=[codex_review(HEAD[:10])],
        threads=[thread(resolved=False, author=HUMAN, body="Cost model matches ROADMAP.")],
    )
    assert api.evaluate().ok


def test_review_of_an_older_commit_fails():
    api = FakeGitHub(reviews=[codex_review("9869703727")])
    verdict = api.evaluate()
    assert not verdict.ok
    assert "9869703727" in verdict.summary
    assert HEAD[:10] in verdict.title


def test_no_codex_activity_at_all_fails():
    verdict = FakeGitHub().evaluate()
    assert not verdict.ok
    assert "@codex review" in verdict.summary
    assert "no review or reaction" in verdict.summary


def test_unresolved_thread_fails_even_when_a_fresh_thumbs_up_exists():
    """CONTRIBUTING.md: do not merge with review comments left unanswered."""
    api = FakeGitHub(
        comments=[{"id": 1, "reactions": {"total_count": 1}}],
        reactions={"issues/comments/1/reactions": [thumbs_up("2026-08-08T12:05:00Z")]},
        threads=[thread(resolved=False)],
    )
    assert not api.evaluate().ok


# --------------------------------------------------------------------------
# Exemptions
# --------------------------------------------------------------------------


def test_small_docs_only_branch_is_exempt():
    api = FakeGitHub(files=["docs/ROADMAP.md", "CONTRIBUTING.md"], changed_lines=30)
    verdict = api.evaluate()
    assert verdict.ok
    assert "Docs-only" in verdict.summary


def test_large_docs_only_branch_is_not_exempt():
    """CONTRIBUTING.md exempts *small* docs branches; a policy rewrite is not one."""
    api = FakeGitHub(
        files=["docs/ROADMAP.md"], changed_lines=check.DOCS_ONLY_MAX_LINES + 1
    )
    assert not api.evaluate().ok


def test_docs_exemption_holds_exactly_at_the_threshold():
    api = FakeGitHub(files=["docs/ROADMAP.md"], changed_lines=check.DOCS_ONLY_MAX_LINES)
    assert api.evaluate().ok


def test_large_docs_only_branch_can_still_use_the_label():
    api = FakeGitHub(
        files=["docs/ROADMAP.md"],
        changed_lines=check.DOCS_ONLY_MAX_LINES + 1,
        labels=["codex-review-not-required"],
    )
    assert api.evaluate().ok


def test_one_code_file_removes_the_docs_exemption():
    api = FakeGitHub(files=["docs/ROADMAP.md", "janasunani/pipeline/run.py"])
    assert not api.evaluate().ok


def test_empty_diff_is_not_treated_as_docs_only():
    assert check.is_docs_only([]) is False


def test_changed_lines_counts_both_sides_of_the_diff():
    files = [{"additions": 10, "deletions": 5}, {"additions": 1, "deletions": 0}]
    assert check.changed_lines(files) == 16


def test_renaming_code_into_docs_is_not_docs_only():
    """A pure rename adds no lines, so the size cap would not catch it either."""
    files = [
        {
            "filename": "docs/run.md",
            "previous_filename": "janasunani/pipeline/run.py",
            "status": "renamed",
            "additions": 0,
            "deletions": 0,
        }
    ]
    assert check.is_docs_only(files) is False


def test_renaming_one_doc_to_another_stays_docs_only():
    files = [
        {
            "filename": "docs/ROADMAP.md",
            "previous_filename": "ROADMAP.md",
            "status": "renamed",
            "additions": 0,
            "deletions": 0,
        }
    ]
    assert check.is_docs_only(files) is True


# --------------------------------------------------------------------------
# The verdict is bound to the sha it was computed against
# --------------------------------------------------------------------------


def test_verdict_carries_the_evaluated_head():
    api = FakeGitHub(reviews=[codex_review(HEAD[:10])], threads=[thread(resolved=True)])
    assert api.evaluate().head_sha == HEAD


def test_check_run_is_posted_to_the_evaluated_head_not_a_refetched_one():
    """A push landing mid-run must not inherit the previous head's pass."""
    old, new = "a" * 40, "b" * 40
    api = FakeGitHub(head_sha=old, reviews=[codex_review(old[:10])], threads=[])
    verdict = api.evaluate()
    assert verdict.ok

    api.head_sha = new  # the branch moves while the gate is still running
    posted: dict[str, Any] = {}

    def fake_request(url, token, method="GET", body=None):
        posted.update(body)
        return {}

    original = check._request
    check._request = fake_request
    try:
        check.post_check_run(REPO, "token", "codex-review", verdict)
    finally:
        check._request = original

    assert posted["head_sha"] == old
    assert posted["conclusion"] == "success"


def test_skip_label_is_exempt():
    api = FakeGitHub(labels=["codex-review-not-required"])
    verdict = api.evaluate()
    assert verdict.ok
    assert "codex-review-not-required" in verdict.summary


def test_draft_pull_requests_pass():
    assert FakeGitHub(draft=True).evaluate().ok


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "login, expected",
    [
        ("chatgpt-codex-connector[bot]", True),  # REST spelling
        ("chatgpt-codex-connector", True),  # GraphQL spelling
        ("github-actions[bot]", False),
        ("ymohanty", False),
        (None, False),
    ],
)
def test_codex_login_is_recognised_in_both_api_spellings(login, expected):
    assert check.is_codex(login) is expected


def test_reviewed_shas_ignores_non_codex_reviews():
    reviews = [
        {"user": HUMAN, "body": "**Reviewed commit:** `deadbeef12`"},
        codex_review("f319c879a1"),
    ]
    assert check.reviewed_shas(reviews) == ["f319c879a1"]


def test_reviewed_shas_keeps_posting_order():
    reviews = [codex_review("aaaaaaaaaa"), codex_review("bbbbbbbbbb")]
    assert check.reviewed_shas(reviews) == ["aaaaaaaaaa", "bbbbbbbbbb"]


def test_thread_label_strips_the_priority_badge():
    label = check._thread_label({"path": "a/b.py", "body": FINDING_BODY})
    assert label == "a/b.py: Exclude failed Sarvam calls from the paired scorecard"


def test_paginated_review_threads_are_all_inspected():
    pages = [
        {
            "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
            "nodes": [thread(resolved=True)],
        },
        {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [thread(resolved=False)],
        },
    ]
    seen: list[str | None] = []

    def graphql(query: str, variables: dict[str, Any]) -> Any:
        seen.append(variables["cursor"])
        page = pages[len(seen) - 1]
        return {"data": {"repository": {"pullRequest": {"reviewThreads": page}}}}

    unresolved = check.unresolved_codex_threads(graphql, "owner", "name", 207)
    assert seen == [None, "cursor1"]
    assert len(unresolved) == 1
