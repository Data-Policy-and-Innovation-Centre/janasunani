"""The sample registry and its guard (#319).

Real code path, stdlib only -- `janasunani.samples` imports nothing, so this
runs in base CI where no extras are installed.
"""

import pytest

from janasunani.samples import (
    REGISTRY,
    MeasurementClass,
    Sample,
    SampleMisuse,
    get_sample,
    require_sample,
)

_DOC = MeasurementClass.DOCUMENT_LEVEL
_POP = MeasurementClass.POPULATION_LEVEL


class TestTheGuard:
    """The check that would have caught 'use the DSI sample for everything,
    including dedup' before the compute rather than after."""

    def test_a_random_draw_is_refused_for_population_level_work(self):
        with pytest.raises(SampleMisuse) as exc:
            require_sample("dsi_large", _POP)
        # The message has to carry *why*, or the next person works around it.
        assert "population-level" in str(exc.value)
        assert "190x" in str(exc.value)

    def test_the_same_sample_is_fine_for_document_level_work(self):
        sample = require_sample("dsi_large", _DOC)
        assert sample.name == "dsi_large"

    def test_a_complete_slice_is_valid_for_both(self):
        assert require_sample("demo_slice", _POP).name == "demo_slice"
        assert require_sample("demo_slice", _DOC).name == "demo_slice"

    def test_the_full_corpus_is_valid_for_population_level_work(self):
        assert require_sample("full_corpus", _POP).name == "full_corpus"

    def test_a_sample_valid_for_nothing_is_refused_both_ways(self):
        # dsi_small overlaps dsi_large by 515 tickets, so combining them
        # double-counts and holding it out leaks.
        for measurement in (_DOC, _POP):
            with pytest.raises(SampleMisuse):
                require_sample("dsi_small", measurement)

    def test_an_unknown_name_lists_the_real_ones(self):
        with pytest.raises(SampleMisuse) as exc:
            get_sample("the_big_one")
        message = str(exc.value)
        assert "dsi_large" in message and "demo_slice" in message


class TestRegistryCoherence:
    """Properties that must hold for every entry, so adding a sample cannot
    quietly re-decide them."""

    def test_no_random_draw_claims_population_level_validity(self):
        offenders = [
            s.name
            for s in REGISTRY.values()
            if s.is_random_draw and _POP in s.valid_for
        ]
        assert offenders == [], (
            f"{offenders} claim population-level validity while being random "
            "draws. Sampling breaks the relationships such a measurement is "
            "about, and size does not fix it."
        )

    def test_every_population_valid_sample_says_why_it_is_complete(self):
        for sample in REGISTRY.values():
            if _POP in sample.valid_for:
                assert not sample.is_random_draw
                assert sample.caveat, f"{sample.name} needs a caveat"

    def test_samples_with_unrecoverable_provenance_say_so(self):
        # pii_gold's release bucket is literally 'unknown'. If that stops
        # being recorded, a number from it gets quoted as if it sat on the
        # same population as everything else.
        assert "unrecoverable" in REGISTRY["pii_gold"].caveat

    def test_every_entry_is_frozen(self):
        sample = REGISTRY["demo_slice"]
        with pytest.raises(Exception):
            sample.name = "something_else"

    def test_registry_rejects_an_incoherent_entry_at_construction(self):
        from janasunani.samples import _check_registry_is_coherent

        bad = Sample(
            name="bad",
            what="a random draw pretending to be complete",
            valid_for=frozenset({_POP}),
            provenance="test",
            is_random_draw=True,
        )
        REGISTRY["bad"] = bad
        try:
            with pytest.raises(SampleMisuse, match="incoherent"):
                _check_registry_is_coherent()
        finally:
            del REGISTRY["bad"]


class TestRecordedFacts:
    """Numbers that were expensive to establish and are easy to lose."""

    def test_dsi_large_records_that_box_is_short_of_s3(self):
        # 189 documents exist in S3 and not in the Box copy. Sourcing from Box
        # silently benchmarks 99.73% of the corpus.
        assert "69,844" in REGISTRY["dsi_large"].caveat
        assert "189" in REGISTRY["dsi_large"].caveat

    def test_dsi_small_records_its_overlap_with_dsi_large(self):
        assert "515" in REGISTRY["dsi_small"].caveat

    def test_dsi_large_pins_its_seed(self):
        assert REGISTRY["dsi_large"].seed == 1337

    def test_complete_samples_have_no_seed(self):
        for name in ("demo_slice", "full_corpus"):
            assert REGISTRY[name].seed is None
