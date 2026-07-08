from datetime import datetime

import polars as pl

from janasunani.serving.history import LakeHistory


def _write_complaints(path):
    pl.DataFrame(
        [
            {
                "ticket_no": "T1",
                "created_on": datetime(2024, 1, 3),
                "district": "Cuttack",
                "category": "Water Supply",
                "subcategory": "Hand pump repair",
                "dept": "Rural Water Supply & Sanitation",
                "status": "Pending",
                "office": "Collector, Cuttack",
                "grievance": "Hand pump is broken",
            },
            {
                "ticket_no": "T2",
                "created_on": datetime(2024, 1, 2),
                "district": "Puri",
                "category": "Electricity",
                "subcategory": "Outage",
                "dept": "Energy",
                "status": "Resolved",
                "office": "Collector, Puri",
                "grievance": "Frequent outage",
            },
            {
                "ticket_no": "T3",
                "created_on": datetime(2024, 1, 1),
                "district": "Cuttack",
                "category": "Roads & Bridges",
                "subcategory": "Pothole repair",
                "dept": "Works",
                "status": "Escalated",
                "office": "Collector, Cuttack",
                "grievance": "Road needs repair",
            },
        ]
    ).write_parquet(path / "complaints.parquet")


def test_lake_history_filters_searches_and_paginates(tmp_path):
    _write_complaints(tmp_path)
    history = LakeHistory(tmp_path)

    page = history.search(district="Cuttack", limit=1, offset=0)
    assert page.total == 2
    assert page.limit == 1
    assert len(page.items) == 1
    assert page.items[0].ticket_no == "T1"  # newest first

    second = history.search(district="Cuttack", limit=1, offset=1)
    assert second.items[0].ticket_no == "T3"

    searched = history.search(q="outage")
    assert searched.total == 1
    assert searched.items[0].ticket_no == "T2"

    category = history.search(category="Water Supply")
    assert category.total == 1
    assert category.items[0].dept == "Rural Water Supply & Sanitation"


def test_lake_history_missing_complaints_file_returns_empty_page(tmp_path):
    page = LakeHistory(tmp_path).search(limit=5, offset=10)

    assert page.items == []
    assert page.total == 0
    assert page.limit == 5
    assert page.offset == 10
