"""Indian person-name span detection (#183), without Presidio.

`tests/test_pii_redaction.py` skips whenever the `pii` extra is absent, which
is the normal state on CI. That is exactly how the gap this recognizer closes
went unmeasured for so long, so the span logic is pure text-in/offsets-out and
is tested here with no optional dependency at all.
"""

from janasunani.pipeline.stages.pii_tagger import _indian_name_spans

NAMES = [
    "Ramesh Kumar Sahoo",
    "Sunita Devi Patra",
    "Bijay Kumar Nayak",
    "Laxmi Priya Behera",
    "Manoj Kumar Rout",
    "Sasmita Mohanty",
    "Prafulla Chandra Jena",
    "Ananya Das",
    "Rabindra Nath Swain",
    "Jagannath Prasad Mishra",
]

FRAMINGS = [
    "{n} reports that the tube well is broken in ward seven.",
    "My name is {n} and the tube well is broken in ward seven.",
    "Applicant: {n}. The tube well is broken in ward seven.",
    "This grievance is filed by {n} of Sambalpur district.",
    "From {n}, resident of ward seven, regarding the broken tube well.",
]


def _covered(text: str) -> str:
    return "".join(text[s:e] for s, e in _indian_name_spans(text))


def test_every_name_token_is_covered_in_every_framing():
    """A partial redaction still identifies, so every token must be covered.

    The measured failure before this recognizer was 42% missed outright and a
    further 5% partial, where the surname was covered and the given name was
    left in the clear.
    """
    leaks = []
    for framing in FRAMINGS:
        for name in NAMES:
            text = framing.format(n=name)
            covered = _covered(text)
            missing = [part for part in name.split() if part not in covered]
            if missing:
                leaks.append((framing, name, missing))
    assert leaks == []


def test_span_covers_the_whole_name_not_just_the_surname():
    text = "Ramesh Kumar Sahoo reports that the tube well is broken."
    spans = _indian_name_spans(text)
    assert spans, "surname gazetteer should have fired"
    start, end = spans[0]
    assert text[start:end] == "Ramesh Kumar Sahoo"


def test_scheme_names_are_not_redacted():
    """Over-redaction destroys the sentence the officer has to act on.

    'Pradhan' is a real Odia surname and the first word of 'Pradhan Mantri
    <scheme>'. Losing the scheme name loses what the grievance is about.
    """
    for text in (
        "Name of the scheme is Pradhan Mantri Awas Yojana and I have not received it.",
        "I applied under Pradhan Mantri Gram Sadak Yojana at the Khordha office.",
    ):
        assert _indian_name_spans(text) == []


def test_pradhan_still_redacts_when_it_is_a_person():
    text = "Sarojini Pradhan of ward seven reports the tube well is broken."
    assert _covered(text) == "Sarojini Pradhan"


def test_places_and_offices_are_not_names():
    """'from' and a bare 'name' are deliberately not introducers.

    Both over-redact on real grievance text: the place in 'the road from
    Sambalpur to Bargarh', and the scheme in 'Name of the scheme is ...'.
    """
    for text in (
        "The road from Sambalpur to Bargarh is broken and needs urgent repair.",
        "I visited the Block Development Office in Sambalpur district on Tuesday.",
        "From the Public Grievance Cell, no reply was received for sixty days.",
        "The Executive Engineer, Rural Water Supply visited Bargarh in March.",
        "I applied under Biju Swasthya Kalyan Yojana at the Khordha office.",
    ):
        assert _indian_name_spans(text) == [], text


def test_titles_introduce_a_name():
    assert _covered("Shri Ramesh Kumar reports the tube well is broken.") == (
        "Ramesh Kumar"
    )
    assert "Sunita" in _covered("Smt. Sunita Devi has not received her pension.")


def test_a_name_does_not_swallow_the_next_sentence():
    """The walk-back stops at anything other than a single space."""
    text = "Applicant: Ananya Das. Bargarh block has not responded."
    covered = _covered(text)
    assert "Ananya Das" in covered
    assert "Bargarh" not in covered


def test_no_spans_on_text_without_names():
    assert _indian_name_spans("The tube well in ward seven has been broken.") == []
