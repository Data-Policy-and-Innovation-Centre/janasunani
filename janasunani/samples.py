"""The registry of named corpora, and the guard that keeps them apart (#319).

Which sample a measurement runs on has been re-litigated repeatedly and got
wrong at least twice. This module exists so it is a lookup rather than a
judgement call.

**The distinction that decides everything here.**

A *document-level* measurement scores each item independently: latency, OCR
accuracy, redaction coverage, summarisation, categorisation. Sampling is
harmless, and a random draw is fine.

A *population-level* measurement asks about relationships *between* records,
so it needs every member of a group present: deduplication, workload, spikes,
themes. Sampling is destructive, and **no random draw can ever satisfy it, at
any size**. `dsi_large` is a 7.29% draw, which leaves a 0.53% chance (p^2)
that both members of a given duplicate pair are present. The *rate* is not
attenuated by p^2, though: the denominator shrinks by p as well, so the
duplicate-member rate measured on the sample is about p of the true one --
roughly 14x too low, not 190x -- and the survivors are a biased rather than
random subset. `demo_slice` was frozen as a *complete district-year* (#64)
precisely because completeness, not size, is the property that matters.

The failure mode this prevents is not exotic. "Use the DSI sample for
everything, including dedup" is a reasonable-sounding instruction that would
have burned hours of compute to produce a confidently wrong number, and
nothing in the codebase would have objected. `require_sample()` objects.

Deliberately dependency-free (stdlib only, no config import) so that any
module can consult it without pulling settings, and so the guard is available
in environments that install no extras.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MeasurementClass(str, Enum):
    """What a measurement needs from its corpus."""

    #: Each item is scored on its own. Sampling is harmless.
    DOCUMENT_LEVEL = "document-level"

    #: Relationships between records. Needs every member of a group present.
    POPULATION_LEVEL = "population-level"


class SampleMisuse(RuntimeError):
    """A measurement asked for a sample that cannot support it.

    Deliberately not a ValueError: this is a design error in the caller, not
    bad input, and it should read as such in a traceback.
    """


@dataclass(frozen=True)
class Sample:
    """One named corpus and what it can honestly be used for."""

    name: str
    what: str
    valid_for: frozenset[MeasurementClass]
    provenance: str
    #: None where the sample is not a draw at all (a complete slice, the corpus).
    seed: Optional[int] = None
    #: True if membership was chosen at random. A random draw is never valid
    #: for POPULATION_LEVEL, and `_check_registry_is_coherent` enforces that
    #: rather than trusting each entry to have been filled in consistently.
    is_random_draw: bool = False
    #: DVC-tracked path, where the bytes or the manifest are pinned.
    dvc_path: Optional[str] = None
    #: Free-text caveat that belongs anywhere this sample is cited.
    caveat: Optional[str] = None

    def supports(self, measurement: MeasurementClass) -> bool:
        return measurement in self.valid_for


_DOC = MeasurementClass.DOCUMENT_LEVEL
_POP = MeasurementClass.POPULATION_LEVEL


REGISTRY: dict[str, Sample] = {
    "demo_slice": Sample(
        name="demo_slice",
        what="Sambalpur/2024, 55,544 complaints, a complete district-year",
        valid_for=frozenset({_DOC, _POP}),
        provenance="#64; frozen in config.DEMO_SLICE_*",
        seed=None,
        is_random_draw=False,
        caveat=(
            "Valid for population-level work only because it is complete. It "
            "is one district and one year, so it is not representative of the "
            "corpus -- a rate measured here describes Sambalpur in 2024."
        ),
    ),
    "dsi_large": Sample(
        name="dsi_large",
        what="70,029 documents across 69,977 tickets, a 7.29% random draw of the corpus",
        valid_for=frozenset({_DOC}),
        provenance=(
            "DSI Clinic Sandbox export, 100,000 tickets sampled at seed 1337; "
            "documents mirrored to janasunani-documents-dsi-reference (#321) "
            "so they are readable without a Glacier restore"
        ),
        seed=1337,
        is_random_draw=True,
        dvc_path="data/external/dsi_reference_manifest.tsv",
        caveat=(
            "Document-level only. Never dedup, workload, spikes or themes: at "
            "7.29% the chance both members of a duplicate pair are present is "
            "0.53%, and since the denominator shrinks by 7.29% too, a "
            "duplicate rate would come out ~14x too low. Note "
            "also that the Box copy of this sample holds 69,844 files against "
            "70,029 here: 189 documents in S3 are absent from Box and 4 files "
            "in Box are not in S3, netting 185. Source from the manifest, not "
            "from Box. The "
            "manifest's md5 column is a true content hash for every object: "
            "the 80 objects over 8 MB arrived multipart in the source bucket "
            "and were rewritten single-part by the server-side copy, so the "
            "reference etags are plain MD5s where the source ones were not."
        ),
    ),
    "dsi_small": Sample(
        name="dsi_small",
        what="6,913 documents, the DSI training set",
        valid_for=frozenset(),
        provenance="DSI Clinic Sandbox export",
        is_random_draw=True,
        caveat=(
            "Registered so its overlap is recorded rather than rediscovered: "
            "515 tickets also appear in dsi_large. Valid for nothing on its "
            "own -- using both together double-counts the overlap, and using "
            "it as a holdout against a model tuned on dsi_large leaks."
        ),
    ),
    "full_corpus": Sample(
        name="full_corpus",
        what="1,371,288 complaints, every record in OLTP",
        valid_for=frozenset({_DOC, _POP}),
        provenance="OLTP complaints table",
        seed=None,
        is_random_draw=False,
        caveat=(
            "The only sample that answers a corpus-wide population question. "
            "Dedup over it needs janasunani-dedup-index --all (#317)."
        ),
    ),
    "pii_gold": Sample(
        name="pii_gold",
        what="480 hand-marked spans across 89 pages of 50 documents",
        valid_for=frozenset({_DOC}),
        provenance="#15; release bucket recorded as 'unknown'",
        dvc_path="data/external/pii_gold_draft_n50.jsonl",
        caveat=(
            "Provenance is unrecoverable: no district and no year were "
            "recorded, so it cannot be stated to sit on any other sample. Any "
            "redaction number from it is measured on a different population "
            "from the latency numbers and must say so."
        ),
    ),
}


def _check_registry_is_coherent() -> None:
    """A random draw can never be valid for population-level work.

    Checked here rather than left to each entry, because the whole point of
    the registry is that the property is not re-decided per sample by whoever
    is adding one.
    """
    for sample in REGISTRY.values():
        if sample.is_random_draw and _POP in sample.valid_for:
            raise SampleMisuse(
                f"registry is incoherent: {sample.name!r} is a random draw and "
                "cannot be valid for population-level measurement. Sampling "
                "breaks the relationships such a measurement is about; size "
                "does not fix it."
            )
        if sample.name != sample.name.strip() or not sample.name:
            raise SampleMisuse(f"registry has a blank or padded name: {sample.name!r}")


_check_registry_is_coherent()


def get_sample(name: str) -> Sample:
    """Look up a registered sample, or fail with the list of real ones."""
    try:
        return REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise SampleMisuse(
            f"unknown sample {name!r}. Registered samples: {known}. Add it to "
            "janasunani/samples.py rather than passing a path around."
        ) from None


def require_sample(name: str, measurement: MeasurementClass) -> Sample:
    """Return the sample, or refuse if it cannot support this measurement.

    This is the guard. Call it from a harness before doing any work, so a
    mismatch costs a traceback rather than hours of compute and a number
    nobody can retract.
    """
    sample = get_sample(name)
    if not sample.supports(measurement):
        valid = ", ".join(sorted(m.value for m in sample.valid_for)) or "nothing"
        raise SampleMisuse(
            f"{name!r} is not valid for {measurement.value} measurement "
            f"(valid for: {valid}). {sample.caveat or ''}".strip()
        )
    return sample
