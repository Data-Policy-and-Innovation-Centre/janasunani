import json

from janasunani.evaluation.pii_scorecard import (
    _normalize_language,
    render_scorecard,
    score_per_language,
)


def _write_gold(tmp_path, records):
    p = tmp_path / "gold.jsonl"
    with p.open("w") as h:
        for r in records:
            h.write(json.dumps(r) + "\n")
    return p


def test_normalize_language():
    assert _normalize_language("en") == "english"
    assert _normalize_language("OR") == "odia"
    assert _normalize_language("roman") == "romanized"
    assert _normalize_language(None) == "unknown"
    assert _normalize_language("translit") == "romanized"


def test_per_language_split_and_thin_slice(tmp_path):
    recs = [
        {"id": "p1", "text": "Ramesh phone 9876543210", "entities": [{"start": 0, "end": 6, "entity": "NAME"}], "language": "en"},
        {"id": "p2", "text": "Sita email sita@example.com", "entities": [{"start": 11, "end": 27, "entity": "EMAIL"}], "language": "or"},
        {"id": "p3", "text": "Bob Aadhaar 123412341234", "entities": [{"start": 12, "end": 24, "entity": "AADHAAR"}], "language": "roman"},
    ]
    gold = _write_gold(tmp_path, recs)
    per = score_per_language(gold)
    # each slice has 1 gold span -> thin (<20)
    assert "english" in per and per["english"].is_low_power
    assert "odia" in per and per["odia"].is_low_power
    assert "romanized" in per and per["romanized"].is_low_power
    # unknown not present when all have language
    assert "unknown" not in per


def test_unknown_when_no_language(tmp_path):
    recs = [
        {"id": "p1", "text": "Ramesh", "entities": [{"start": 0, "end": 6, "entity": "NAME"}]},
    ]
    gold = _write_gold(tmp_path, recs)
    per = score_per_language(gold)
    assert "unknown" in per


def test_render_includes_dsi_reference_and_missed_rate(tmp_path):
    recs = [
        {"id": "p1", "text": "Ramesh", "entities": [{"start": 0, "end": 6, "entity": "NAME"}], "language": "en"},
    ]
    gold = _write_gold(tmp_path, recs)
    text = render_scorecard(gold)
    assert "missed" in text.lower()
    assert "80.56%" in text or "80.6" in text
    assert "low power" in text.lower() or "thin slice" in text.lower()
