"""Language gate of scripts/sample_english_complaints.py.

The script is a single file in scripts/ (not a package), so it's loaded via
importlib. langdetect lives in the pipeline extras — skip where absent (CI).
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("langdetect")

_SCRIPT = Path(__file__).parents[1] / "scripts" / "sample_english_complaints.py"
spec = importlib.util.spec_from_file_location("sample_english_complaints", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_english_subject_accepted():
    assert mod.is_english(
        "The water supply in our ward has been broken for three weeks "
        "and nobody from the block office has come to repair it."
    )


def test_odia_script_rejected():
    assert not mod.is_english(
        "ଆମ ଗାଁର ପାଣି ସମସ୍ୟା ବିଷୟରେ ଅଭିଯୋଗ କରୁଛି ଦୟାକରି ସମାଧାନ କରନ୍ତୁ"
    )


def test_mixed_script_rejected():
    assert not mod.is_english(
        "Complaint regarding ପାଣି ସମସ୍ୟା in our village please resolve the matter"
    )


def test_romanized_odia_rejected():
    # No English function words — the stopword gate rejects it regardless of
    # what langdetect guesses.
    assert not mod.is_english(
        "mo ghara pakhare nala safai heu nahin bahut asubidha "
        "hauchi daya kari samadhana karantu"
    )


def test_short_subject_rejected():
    assert not mod.is_english("water problem")
