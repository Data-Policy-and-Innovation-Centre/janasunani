from datetime import datetime

from scripts.sample_categorization_benchmark import prepare_records


def test_preparation_is_chronological_group_disjoint_and_excludes_conflicts():
    rows = []
    for category in ("Housing", "Health"):
        for month in (1, 7, 10):
            for index in range(2):
                rows.append(
                    (
                        f"{category}-{month}-{index}",
                        datetime(2024, month, 1),
                        f"{category} distinct grievance {month} {index}",
                        category,
                    )
                )
    rows.extend(
        [
            ("repeat-early", datetime(2024, 1, 1), "same campaign", "Housing"),
            ("repeat-late", datetime(2024, 10, 1), "same campaign", "Housing"),
            ("conflict-a", datetime(2024, 1, 1), "conflicting text", "Housing"),
            ("conflict-b", datetime(2024, 7, 1), "conflicting text", "Health"),
            ("pii", datetime(2024, 1, 1), "call 9876543210", "Housing"),
        ]
    )

    selected, evidence = prepare_records(
        rows, salt="s" * 32, min_support_per_split=2
    )

    assert set(row["category"] for row in selected) == {"Housing", "Health"}
    assert len({row["group_id"] for row in selected}) == len(selected)
    assert evidence["conflicting_label_groups_excluded"] == 1
    assert evidence["shaped_pii_rows_excluded"] == 1
    same_campaign = [row for row in selected if row["redacted_text"] == "same campaign"]
    assert len(same_campaign) == 1
    assert same_campaign[0]["split"] == "train"
