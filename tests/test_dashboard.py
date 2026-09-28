"""The supervisor dashboard, from a fixture lake through the publisher to the API.

Every case below is synthetic and arranged so each rule has one countable
answer: the age-bucket edges, "awaiting assignment", the entry-office filter,
the queue order, the ticket timeline, and phase means that add up to the mean.
"""

import os
import shutil
from datetime import date, datetime

import polars as pl
import pytest
from fastapi.testclient import TestClient

from janasunani.analytics import dashboard as publisher
from janasunani.serving import dashboard as provider_module
from janasunani.serving.api import create_app
from janasunani.serving.dashboard import DashboardProvider

AS_OF = date(2025, 7, 30)
FY24 = {"year": "2024-25"}
CM, COLLECTOR = "Office of Chief Minister", "Collector"

# ticket, created, resolved, status, office, dept, category
_COMPLAINTS = [
    # Open. The latest filing sets the snapshot date: 0 days, no action yet.
    ("O0", datetime(2025, 7, 30), None, "Pending", CM, "Revenue", "Land"),
    ("O30", datetime(2025, 6, 30), None, "Pending", CM, "Revenue", "Land"),
    # A slash, as in real ticket numbers like OR159/P/2021/00535.
    ("OR/31", datetime(2025, 6, 29), None, "Pending", CM, "Revenue", "Land"),
    ("O45", datetime(2025, 6, 15), None, "Pending", COLLECTOR, "Health", "Hospital"),
    # Awaiting assignment but younger than O45, so the two sort keys disagree.
    ("O40", datetime(2025, 6, 20), None, "Pending", COLLECTOR, "Health", "Hospital"),
    ("O60", datetime(2025, 5, 31), None, "Pending", COLLECTOR, "Health", "Hospital"),
    ("O61", datetime(2025, 5, 30), None, "Pending", None, "Health", "Hospital"),
    # Disposed, filed FY 2024-25.
    ("D1", datetime(2024, 8, 1), datetime(2024, 8, 30), "Disposed", CM, "Revenue", "Land"),
    ("D2", datetime(2024, 9, 1), datetime(2024, 9, 21), "Disposed", CM, "Revenue", "Land"),
    ("D3", datetime(2024, 10, 1), datetime(2024, 10, 10), "Disposed", COLLECTOR, "Health", "Hospital"),
    # Two more quick Health cases: Health is now the larger department and
    # still the faster, so sorting by volume and by speed disagree.
    ("D5", datetime(2024, 11, 1), datetime(2024, 11, 10), "Disposed", COLLECTOR, "Health", "Hospital"),
    ("D6", datetime(2024, 12, 1), datetime(2024, 12, 11), "Disposed", COLLECTOR, "Health", "Hospital"),
    # Disposed but filed FY 2023-24: outside the disposed base.
    ("D4", datetime(2024, 3, 1), datetime(2024, 3, 11), "Disposed", CM, "Revenue", "Land"),
    # Discarded: neither open nor disposed.
    ("X1", datetime(2025, 7, 1), datetime(2025, 7, 2), "Discard", CM, None, None),
]

_CELL, _COLL, _BDO = "CM Grievance Cell, Bhubaneswar", "Collector, Puri", "BDO, Sadar"
# ticket, date, status, office
_ACTIONS = [
    ("O30", datetime(2025, 6, 30), "Complaint Register", _CELL),
    ("OR/31", datetime(2025, 6, 29), "Complaint Register", _CELL),
    ("OR/31", datetime(2025, 7, 10), "Forwarded", _COLL),
    ("O45", datetime(2025, 6, 16), "Forwarded", _COLL),
    ("O45", datetime(2025, 6, 20), "Forwarded", _BDO),
    ("O40", datetime(2025, 6, 21), "Forwarded", _COLL),
    ("O60", datetime(2025, 6, 1), "Forwarded", _COLL),
    ("O60", datetime(2025, 6, 5), "Forwarded", _COLL),
    # D1: registration 1, first assignment 3, field action 20, review 0,
    # closure 5 = 29 days.
    ("D1", datetime(2024, 8, 2), "Complaint Register", _CELL),
    ("D1", datetime(2024, 8, 5), "Forwarded", _COLL),
    ("D1", datetime(2024, 8, 15), "Forwarded", _BDO),
    ("D1", datetime(2024, 8, 25), "Replied", _COLL),
    # D2: first assignment 10, closure 10 = 20 days.
    ("D2", datetime(2024, 9, 1), "Complaint Register", _CELL),
    ("D2", datetime(2024, 9, 11), "Forwarded", _COLL),
    # D3: registration 1, first assignment 2, closure 6 = 9 days.
    ("D3", datetime(2024, 10, 2), "Forwarded", _COLL),
    ("D3", datetime(2024, 10, 4), "Forwarded", _BDO),
    ("D4", datetime(2024, 3, 2), "Forwarded", _COLL),
    # D5 and D6 take D3's route: 9 and 10 days.
    ("D5", datetime(2024, 11, 2), "Forwarded", _COLL),
    ("D5", datetime(2024, 11, 4), "Forwarded", _BDO),
    ("D6", datetime(2024, 12, 2), "Forwarded", _COLL),
    ("D6", datetime(2024, 12, 4), "Forwarded", _BDO),
]


def _write_lake(path, actions=_ACTIONS):
    path.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        _COMPLAINTS,
        schema=[("ticket_no", pl.Utf8), ("created_on", pl.Datetime),
                ("resolved_on", pl.Datetime), ("status", pl.Utf8), ("office", pl.Utf8),
                ("dept", pl.Utf8), ("category", pl.Utf8)],
        orient="row",
    ).with_columns(
        mode=pl.lit("m"), mode_id=pl.lit(1), dept_id=pl.lit(12), subcategory=pl.lit("s"),
        district=pl.lit("Puri"), transfer_status=pl.lit("No"),
        # D2 is the one disposal that reached the citizen.
        benefitted=pl.when(pl.col("ticket_no") == "D2").then(pl.lit("Yes")).otherwise(pl.lit("No")),
    ).write_parquet(path / "complaints.parquet")
    pl.DataFrame(
        [(i + 1, t, d, s, f"{s} - {o}") for i, (t, d, s, o) in enumerate(actions)],
        schema=[("id", pl.Int64), ("ticket_no", pl.Utf8), ("action_taken_date", pl.Datetime),
                ("action_status", pl.Utf8), ("complaint_status_with_authority", pl.Utf8)],
        orient="row",
    ).write_parquet(path / "action_history.parquet")


@pytest.fixture(scope="module")
def release(tmp_path_factory):
    lake_dir = tmp_path_factory.mktemp("lake")
    _write_lake(lake_dir)
    out = tmp_path_factory.mktemp("release")
    con = publisher.open_lake(lake_dir)
    try:
        publisher.build(con, out)
    finally:
        con.close()
    return out


@pytest.fixture
def client(release):
    return TestClient(create_app(dashboard=DashboardProvider(release)))


def test_meta_carries_the_snapshot_date_and_the_entry_offices(client):
    meta = client.get("/dashboard/meta").json()
    assert meta["asOf"] == "2025-07-30"
    # Newest first; the extract ends 30 July 2025, so 2025-26 is a part year.
    assert [y["id"] for y in meta["years"]] == ["2025-26", "2024-25", "2023-24"]
    assert meta["years"][0]["label"] == "FY 2025-26 (part year)"
    ids = [o["id"] for o in meta["offices"]]
    assert {"cm-office", "collector", "not-recorded"} <= set(ids)


def test_status_tree_adds_up_and_matches_the_tabs(client):
    tree = client.get("/dashboard/status").json()
    # 7 open, 6 disposed (D1-D6), 1 discarded (X1).
    assert (tree["total"], tree["open"], tree["disposed"], tree["discarded"]) == (14, 7, 6, 1)
    assert tree["open"] + tree["disposed"] + tree["discarded"] == tree["total"]
    # D2's benefit is part of the disposed branch, not a fourth outcome.
    assert tree["disposedWithBenefit"] == 1
    # The Open branch is exactly what the Live tab counts.
    assert tree["open"] == client.get("/dashboard/live").json()["open"]


def test_status_tree_follows_office_and_year(client):
    fy25 = client.get("/dashboard/status", params={"year": "2025-26"}).json()
    # O0 open and X1 discarded, both filed in July 2025.
    assert (fy25["total"], fy25["open"], fy25["discarded"]) == (2, 1, 1)
    cm = client.get("/dashboard/status", params={"office": "cm-office", "year": "2024-25"}).json()
    # O30, OR/31 open; D1, D2 disposed.
    assert (cm["total"], cm["open"], cm["disposed"]) == (4, 2, 2)


def test_age_buckets_are_inclusive_at_30_and_60(client):
    live = client.get("/dashboard/live").json()
    assert live["open"] == 7
    counts = {b["id"]: b["count"] for b in live["buckets"]}
    # O0, O30 | OR/31, O40, O45, O60 | O61
    assert counts == {"0-30": 2, "31-60": 4, "61+": 1}


def test_office_filter_narrows_to_the_entry_office(client):
    cm = client.get("/dashboard/live", params={"office": "cm-office"}).json()
    assert cm["open"] == 3
    unrecorded = client.get("/dashboard/live", params={"office": "not-recorded"}).json()
    assert {b["id"]: b["count"] for b in unrecorded["buckets"]}["61+"] == 1


def test_queue_puts_awaiting_assignment_first_then_the_oldest(client):
    page = client.get("/dashboard/live/queue", params={"bucket": "31-60"}).json()
    got = [(i["ticketNo"], i["awaitingAssignment"], i["daysOpen"]) for i in page["items"]]
    # O60 and O40 have only ever been with the Collector; O45 and OR/31 have
    # moved on. Awaiting comes first even when it is the younger case.
    assert got == [("O60", True, 60), ("O40", True, 40), ("O45", False, 45),
                   ("OR/31", False, 31)]
    assert page["total"] == 4
    item = page["items"][0]
    assert (item["category"], item["dept"]) == ("Hospital", "Health")


def _queue(client, **params):
    return client.get("/dashboard/live/queue", params={"bucket": "31-60", **params}).json()


def test_awaiting_filter_hides_or_keeps_only_the_awaiting(client):
    assert [i["ticketNo"] for i in _queue(client, awaiting="hide")["items"]] == ["O45", "OR/31"]
    only = _queue(client, awaiting="only")
    assert [i["ticketNo"] for i in only["items"]] == ["O60", "O40"]
    assert only["total"] == 2


def test_label_filters_narrow_the_queue_and_facets_cross_filter(client):
    page = _queue(client, category="Land")
    assert [i["ticketNo"] for i in page["items"]] == ["OR/31"]
    facets = page["facets"]
    # A facet ignores its own choice, so every category stays pickable...
    assert facets["categories"] == [{"label": "Hospital", "count": 3},
                                    {"label": "Land", "count": 1}]
    # ...while the others count only what the chosen category leaves.
    assert facets["depts"] == [{"label": "Revenue", "count": 1}]
    assert (facets["awaiting"], facets["notAwaiting"]) == (0, 1)


def test_facet_counts_follow_the_awaiting_filter(client):
    facets = _queue(client, awaiting="hide")["facets"]
    assert {f["label"]: f["count"] for f in facets["depts"]} == {"Health": 1, "Revenue": 1}
    # The awaiting split itself ignores the awaiting filter.
    assert (facets["awaiting"], facets["notAwaiting"]) == (2, 2)


def test_a_case_with_no_action_is_awaiting_assignment(client):
    page = client.get("/dashboard/live/queue", params={"bucket": "0-30"}).json()
    assert {i["ticketNo"]: i["awaitingAssignment"] for i in page["items"]} == {
        "O0": True, "O30": True}


def test_timeline_marks_the_current_step_and_counts_to_the_snapshot(client):
    # Encoded as the frontend sends it; the slash must not split the route.
    tl = client.get("/dashboard/ticket/OR%2F31/timeline").json()
    assert tl["daysOpen"] == 31
    steps = [(s["office"], s["days"], s["current"]) for s in tl["steps"]]
    assert steps == [(_CELL, 11, False), (_COLL, 20, True)]


def test_disposed_overall_phases_add_up_to_the_mean(client):
    (row,) = client.get("/dashboard/disposed", params=FY24).json()["rows"]
    # D1 29, D2 20, D3 9, D5 9, D6 10 days; D4 is outside FY 2024-25.
    assert row["n"] == 5
    assert row["meanDays"] == pytest.approx(15.4, abs=0.05)
    # One number per row: the average, which the stages add up to.
    assert "medianDays" not in row
    assert sum(row["phases"].values()) == pytest.approx(row["meanDays"], abs=0.1)


def test_departments_sort_slowest_first_and_toggle_to_fastest(client):
    slow = client.get("/dashboard/disposed", params={**FY24, "level": "dept"}).json()["rows"]
    fast = client.get("/dashboard/disposed", params={**FY24, "level": "dept", "order": "fastest"}).json()["rows"]
    assert [r["label"] for r in slow] == ["Revenue", "Health"]
    assert [r["label"] for r in fast] == ["Health", "Revenue"]
    by_volume = client.get("/dashboard/disposed", params={**FY24, "level": "dept", "order": "volume"}).json()["rows"]
    # Health is larger (three cases to two) but faster, so volume puts it first.
    assert [(r["label"], r["n"]) for r in by_volume] == [("Health", 3), ("Revenue", 2)]
    assert slow[0]["meanDays"] == 24.5


def test_categories_are_within_the_chosen_department(client):
    rows = client.get("/dashboard/disposed", params={**FY24, "level": "category", "dept": "Health"}).json()["rows"]
    assert [(r["label"], r["n"]) for r in rows] == [("Hospital", 3)]


def _routes(client, **params):
    return client.get("/dashboard/disposed/routes", params={**FY24, **params}).json()


def test_routes_under_the_case_floor_fold_into_other(client):
    routes = _routes(client)
    # Three routes of one case each: none clears the ten-case floor, so all
    # three are one "Other" row.
    (other,) = routes["rows"]
    assert (other["label"], other["n"], other["meanDays"]) == ("Other routes (3 routes)", 5, 15.4)
    assert routes["total"] == 5 and routes["minRouteN"] == 10


def test_routes_are_named_until_they_cover_the_cases(client, monkeypatch):
    monkeypatch.setattr(provider_module, "MIN_ROUTE_N", 1)
    assert [r["n"] for r in _routes(client)["rows"]] == [3, 1, 1]
    # At 50% coverage the largest route (60%) is enough and the rest fold.
    monkeypatch.setattr(provider_module, "ROUTES_COVER", 0.5)
    rows = _routes(client)["rows"]
    assert [r["label"] for r in rows] == ["Collector > BDO", "Other routes (2 routes)"]


def test_routes_sort_by_speed_or_volume_with_other_always_last(client, monkeypatch):
    monkeypatch.setattr(provider_module, "MIN_ROUTE_N", 1)
    monkeypatch.setattr(provider_module, "ROUTES_MAX", 2)
    slow = _routes(client, order="slowest")["rows"]
    fast = _routes(client, order="fastest")["rows"]
    # Two named routes (the cap), then Other, whatever the order.
    assert [r["meanDays"] for r in slow[:2]] == sorted((r["meanDays"] for r in slow[:2]), reverse=True)
    assert [r["label"] for r in fast[:2]] == [r["label"] for r in reversed(slow[:2])]
    assert slow[-1]["label"] == fast[-1]["label"] == "Other routes (1 route)"
    assert sum(r["n"] for r in slow) == 5
    volume = _routes(client, order="volume")["rows"]
    assert [r["n"] for r in volume] == [3, 1, 1]


def test_routes_filter_by_department_and_category(client):
    routes = client.get("/dashboard/disposed/routes", params={**FY24, "office": "cm-office", "dept": "Revenue",
                                "category": "Land"}).json()
    assert sum(r["n"] for r in routes["rows"]) == 2


def test_disposed_all_years_adds_the_earlier_year(client):
    everything = client.get("/dashboard/disposed").json()
    fy23 = client.get("/dashboard/disposed", params={"year": "2023-24"}).json()
    # The fixture holds only March 2024 of 2023-24, so it is a part year.
    assert everything["period"] == "All years"
    assert fy23["period"] == "Filed FY 2023-24 (part year)"
    # D4 (10 days, filed March 2024) is the only 2023-24 case.
    assert [(r["n"], r["meanDays"]) for r in fy23["rows"]] == [(1, 10.0)]
    assert everything["rows"][0]["n"] == 6


def test_live_year_filter_counts_only_that_years_filings(client):
    # O0 was filed on the snapshot day, the first day of 2025-26.
    assert client.get("/dashboard/live", params={"year": "2025-26"}).json()["open"] == 1
    assert client.get("/dashboard/live", params=FY24).json()["open"] == 6
    page = client.get("/dashboard/live/queue", params={"bucket": "0-30", "year": "2025-26"}).json()
    assert [i["ticketNo"] for i in page["items"]] == ["O0"]


def test_unknown_office_is_404_and_a_bad_ticket_is_422(client):
    assert client.get("/dashboard/live", params={"office": "nowhere"}).status_code == 404
    assert client.get("/dashboard/live", params={"year": "1999-00"}).status_code == 404
    assert client.get("/dashboard/live", params={"year": "2024"}).status_code == 422
    assert client.get("/dashboard/ticket/D1/timeline").status_code == 404  # not open
    assert client.get("/dashboard/ticket/a%20b/timeline").status_code == 422
    assert client.get("/dashboard/live/queue", params={"bucket": "90+"}).status_code == 422
    assert client.get("/dashboard/live/queue",
                      params={"bucket": "0-30", "awaiting": "maybe"}).status_code == 422


def test_snapshot_date_is_the_last_filing_or_action(tmp_path):
    # An action after the last filing is still inside the extract.
    _write_lake(tmp_path / "lake", _ACTIONS + [("OR/31", datetime(2025, 8, 2), "Forwarded", _BDO)])
    con = publisher.open_lake(tmp_path / "lake")
    try:
        meta = publisher.build(con, tmp_path / "release")
    finally:
        con.close()
    assert meta["as_of"] == "2025-08-02"


def _copy(release, dest):
    shutil.copytree(release, dest)
    return dest


def test_a_republished_release_is_served_without_a_restart(release, tmp_path):
    rel = _copy(release, tmp_path / "rel")
    client = TestClient(create_app(dashboard=DashboardProvider(rel)))
    assert client.get("/dashboard/meta").json()["asOf"] == "2025-07-30"
    meta = rel / "meta.json"
    meta.write_text(meta.read_text().replace("2025-07-30", "2025-07-31", 1))
    st = meta.stat()
    os.utime(meta, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert client.get("/dashboard/meta").json()["asOf"] == "2025-07-31"


def test_an_unreadable_release_is_503_not_500(release, tmp_path):
    rel = _copy(release, tmp_path / "rel")
    (rel / "open_cases.parquet").write_text("not parquet")
    client = TestClient(create_app(dashboard=DashboardProvider(rel)))
    resp = client.get("/dashboard/live")
    assert resp.status_code == 503
    assert "unreadable" in resp.json()["detail"]


def test_missing_release_is_503_not_made_up_values(tmp_path):
    client = TestClient(create_app(dashboard=DashboardProvider(tmp_path / "none")))
    resp = client.get("/dashboard/live")
    assert resp.status_code == 503
    assert "janasunani-publish-dashboard" in resp.json()["detail"]
