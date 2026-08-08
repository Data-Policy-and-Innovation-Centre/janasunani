"""Demo slice determinism — #64 pre-commit.

Sambalpur/2024 is frozen via #64 (highest-volume district×year, 55,544 with
grievance text, no ED override). The box backfill (#71) already indexed it
(55,544 signatures, 10,963 groups at 07 Aug 14:14). This test locks the
constant so a later change to the slice predicate fails loudly rather than
silentlyCTV.
"""

from janasunani.config import (
    DEMO_SLICE_DISTRICT,
    DEMO_SLICE_GROUPS,
    DEMO_SLICE_LABEL,
    DEMO_SLICE_SIZE,
    DEMO_SLICE_YEAR,
)


def test_demo_slice_is_sambalpur_2024() -> None:
    assert DEMO_SLICE_DISTRICT == "Sambalpur"
    assert DEMO_SLICE_YEAR == 2024
    assert DEMO_SLICE_LABEL == "Sambalpur/2024"
    assert DEMO_SLICE_SIZE == 55544
    assert DEMO_SLICE_GROUPS == 10963


def test_demo_slice_label_is_district_slash_year() -> None:
    assert DEMO_SLICE_LABEL == f"{DEMO_SLICE_DISTRICT}/{DEMO_SLICE_YEAR}"


def test_demo_slice_sizes_are_positive_and_consistent() -> None:
    # groups cannot exceed signatures; both must be >0 and match the box run
    assert DEMO_SLICE_SIZE > 0
    assert DEMO_SLICE_GROUPS > 0
    assert DEMO_SLICE_GROUPS < DEMO_SLICE_SIZE
