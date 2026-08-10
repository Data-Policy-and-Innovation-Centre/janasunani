"""S3 document source for the benchmark sample draw.

Split from ``sarvam_sample_builder`` so the draw itself has no hard dependency
on boto3 or on the network, and so the whole selection path can be tested
against a fake source. This module is the only part that talks to S3.

The operational fact this exists to handle: roughly **90% of the document
corpus is in GLACIER**, where ``GetObject`` fails outright with
``InvalidObjectState``. A restore stages a temporary readable copy, and the
window has to outlast whatever the sample is for. A 3-day window set on
9 August 2026 expired on the 13th, the day before the demo it was built for,
which is why ``restore_days`` defaults high rather than low.

Reading is one-directional: this module downloads documents and requests
restores. It never writes objects, and it never deletes them.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from loguru import logger

DEFAULT_BUCKET = "janasunani-documents-main"
DEFAULT_REGION = "ap-south-1"

#: No named profile by default: the documented EC2 hosts authenticate to S3
#: via a shared IAM instance role and have no local AWS profile installed
#: (docs/DEPLOY.md: "S3 access is via the instance role -- no static keys on
#: the boxes"). Passing a profile name here forces boto3 past that chain and
#: onto a local profile lookup, which raises ``ProfileNotFound`` on those
#: boxes. Leave it unset so ``boto3.Session`` falls through to the instance
#: role; a local user who wants a named profile can still pass one.
DEFAULT_PROFILE = None

#: Expedited Glacier retrieval is minutes; Standard is hours. On a deadline,
#: the difference is a coffee break against a lost day.
DEFAULT_RESTORE_TIER = "Expedited"

#: Long enough that a sample outlives the thing it was drawn for.
DEFAULT_RESTORE_DAYS = 21

#: AWS's documented Glacier retrieval windows, padded for headroom, keyed by
#: restore tier: Expedited is minutes, Standard is 3-5 hours, Bulk is 5-12
#: hours. A single fixed timeout (the old default was 1,200s) is only long
#: enough for Expedited; under Standard or Bulk it always expires first and
#: every archived object -- roughly 90% of this corpus -- is silently
#: excluded from the manifest.
DEFAULT_RESTORE_TIMEOUT_SECONDS: dict[str, int] = {
    "Expedited": 15 * 60,
    "Standard": 6 * 3600,
    "Bulk": 13 * 3600,
}


class S3DocumentSource:
    """Find, restore and fetch documents for a ticket.

    Satisfies ``sarvam_sample_builder.DocumentSource``.
    """

    def __init__(
        self,
        *,
        bucket: str = DEFAULT_BUCKET,
        region: str = DEFAULT_REGION,
        profile: str | None = DEFAULT_PROFILE,
        restore_days: int = DEFAULT_RESTORE_DAYS,
        restore_tier: str = DEFAULT_RESTORE_TIER,
        poll_seconds: int = 30,
        restore_timeout_seconds: int | None = None,
        client: Any | None = None,
        sleep: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.restore_days = restore_days
        self.restore_tier = restore_tier
        self.poll_seconds = poll_seconds
        # No explicit override: wait as long as the selected tier needs, not
        # a one-size-fits-all figure that only suits Expedited.
        self.restore_timeout_seconds = (
            restore_timeout_seconds
            if restore_timeout_seconds is not None
            else DEFAULT_RESTORE_TIMEOUT_SECONDS.get(
                restore_tier, DEFAULT_RESTORE_TIMEOUT_SECONDS["Bulk"]
            )
        )
        self._sleep = sleep or time.sleep
        self._client = client
        self._region = region
        self._profile = profile

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3

            session = boto3.Session(profile_name=self._profile, region_name=self._region)
            self._client = session.client("s3")
        return self._client

    def find(self, ticket: str) -> tuple[str, str] | None:
        """The first object whose key starts ``{ticket}_``, with its class."""
        response = self.client.list_objects_v2(
            Bucket=self.bucket, Prefix=f"{ticket}_", MaxKeys=1
        )
        contents = response.get("Contents") or []
        if not contents:
            return None
        return contents[0]["Key"], contents[0].get("StorageClass", "STANDARD")

    def _restore_state(self, key: str) -> str:
        """``ready``, ``in_progress`` or ``needs_restore``."""
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        if head.get("StorageClass", "STANDARD") == "STANDARD":
            return "ready"
        restore = head.get("Restore")
        if restore is None:
            return "needs_restore"
        return "in_progress" if 'ongoing-request="true"' in restore else "ready"

    def _request_restore(self, key: str) -> str:
        import botocore.exceptions

        try:
            self.client.restore_object(
                Bucket=self.bucket,
                Key=key,
                RestoreRequest={
                    "Days": self.restore_days,
                    "GlacierJobParameters": {"Tier": self.restore_tier},
                },
            )
            return "in_progress"
        except botocore.exceptions.ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "RestoreAlreadyInProgress":
                return "in_progress"
            if code == "InvalidObjectState":
                # Already restored, or not archived after all.
                return "ready"
            raise

    def ensure_readable(self, keys: Sequence[str]) -> set[str]:
        """Restore archived keys and wait. Returns those still unavailable.

        Returning rather than raising is deliberate: one object stuck in a
        restore should cost the sample that document, not the whole run.
        """
        pending: set[str] = set()
        for key in keys:
            state = self._restore_state(key)
            if state == "needs_restore":
                state = self._request_restore(key)
            if state == "in_progress":
                pending.add(key)

        if not pending:
            return set()

        logger.info(
            "restore requested ({}) for {} object(s)", self.restore_tier, len(pending)
        )
        waited = 0
        while pending and waited < self.restore_timeout_seconds:
            self._sleep(self.poll_seconds)
            waited += self.poll_seconds
            pending = {k for k in pending if self._restore_state(k) != "ready"}
            logger.info("  {}s: {} still staging", waited, len(pending))
        if pending:
            logger.warning(
                "{} object(s) still staging after {}s; excluding them",
                len(pending),
                waited,
            )
        return pending

    def fetch(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))
