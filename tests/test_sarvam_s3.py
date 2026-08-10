"""Tests for the S3 document source.

Never makes a live AWS call: ``client`` is always stubbed in, so nothing here
touches boto3's network path or requires credentials.
"""

from __future__ import annotations

import pytest

from janasunani.evaluation.sarvam_s3 import (
    DEFAULT_RESTORE_TIMEOUT_SECONDS,
    S3DocumentSource,
)


def test_default_profile_is_unset_so_the_instance_role_chain_applies():
    """Codex finding on #233: the deployed boxes have no 'Janasunani' profile.

    docs/DEPLOY.md: "S3 access is via the instance role -- no static keys on
    the boxes." ``boto3.Session(profile_name=None, ...)`` falls through to
    the default credential chain, which resolves to that instance role on
    those hosts. A hardcoded profile name instead forces a local profile
    lookup that raises ``ProfileNotFound`` there.
    """
    source = S3DocumentSource()
    assert source._profile is None


def test_a_local_user_can_still_opt_into_a_named_profile():
    source = S3DocumentSource(profile="MyLocalProfile")
    assert source._profile == "MyLocalProfile"


@pytest.mark.parametrize(
    "tier, hours_floor",
    [("Expedited", 0), ("Standard", 3), ("Bulk", 5)],
)
def test_default_timeout_covers_the_documented_retrieval_window(tier, hours_floor):
    """Codex finding on #233: a fixed 1,200s timeout only suits Expedited.

    Standard Glacier retrieval is documented at 3-5 hours and Bulk at 5-12;
    the old fixed 20-minute timeout always expired first under those tiers
    and silently dropped every archived object from the manifest.
    """
    source = S3DocumentSource(restore_tier=tier)
    assert source.restore_timeout_seconds >= hours_floor * 3600


def test_standard_and_bulk_wait_longer_than_expedited():
    expedited = S3DocumentSource(restore_tier="Expedited").restore_timeout_seconds
    standard = S3DocumentSource(restore_tier="Standard").restore_timeout_seconds
    bulk = S3DocumentSource(restore_tier="Bulk").restore_timeout_seconds
    assert expedited < standard < bulk


def test_an_explicit_timeout_overrides_the_tier_default():
    source = S3DocumentSource(restore_tier="Standard", restore_timeout_seconds=60)
    assert source.restore_timeout_seconds == 60


def test_an_unknown_tier_falls_back_to_the_longest_default():
    """Fail safe rather than fail fast: an unrecognised tier should wait too
    long rather than too little and silently drop archived objects."""
    source = S3DocumentSource(restore_tier="SomeNewTier")
    assert source.restore_timeout_seconds == DEFAULT_RESTORE_TIMEOUT_SECONDS["Bulk"]


def test_ensure_readable_waits_out_the_tier_default_before_giving_up():
    """A restore that never completes should be polled for the whole
    tier-appropriate window, not the old fixed 1,200s."""

    class _NeverReadyClient:
        def head_object(self, Bucket, Key):
            return {"StorageClass": "GLACIER", "Restore": 'ongoing-request="true"'}

    waits: list[int] = []
    source = S3DocumentSource(
        restore_tier="Standard",
        poll_seconds=1800,  # 30 minutes, so the loop is short even over a multi-hour timeout
        client=_NeverReadyClient(),
        sleep=lambda s: waits.append(s),
    )
    pending = source.ensure_readable(["archived.pdf"])
    assert pending == {"archived.pdf"}
    assert sum(waits) >= DEFAULT_RESTORE_TIMEOUT_SECONDS["Standard"]


def test_ensure_readable_stops_polling_once_the_object_is_ready():
    calls = {"n": 0}

    class _EventuallyReadyClient:
        def head_object(self, Bucket, Key):
            calls["n"] += 1
            if calls["n"] < 3:
                return {"StorageClass": "GLACIER", "Restore": 'ongoing-request="true"'}
            return {"StorageClass": "GLACIER", "Restore": 'ongoing-request="false"'}

    source = S3DocumentSource(
        restore_tier="Expedited",
        poll_seconds=1,
        client=_EventuallyReadyClient(),
        sleep=lambda s: None,
    )
    pending = source.ensure_readable(["archived.pdf"])
    assert pending == set()
