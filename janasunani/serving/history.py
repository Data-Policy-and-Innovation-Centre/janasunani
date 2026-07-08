"""History providers behind ``GET /history``.

The real one (wire-up) reads the Parquet lake via ``olap/lake.py`` — history
is *historical* data, refreshed by re-materialization, per the roadmap's
freshness decision. The skeleton ships ``MockHistory``: ~120 deterministic
fake rows in the lake's column shape, with the same filter semantics the real
provider will honor, so the frontend's browse/search UX is built against
working pagination and filters.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Protocol

from janasunani.olap import lake
from janasunani.serving.schemas import HistoryItem, HistoryPage

_DISTRICTS = ("Khordha", "Cuttack", "Ganjam", "Sundargarh", "Balasore")
_STATUSES = ("Resolved", "Pending", "In Progress", "Escalated")
_CATEGORIES = (
    ("Drinking Water Supply", "Hand pump repair", "Rural Water Supply & Sanitation"),
    ("Electricity", "Frequent outage", "Energy"),
    ("Roads & Bridges", "Pothole repair", "Works"),
    ("Public Health", "PHC staffing", "Health & Family Welfare"),
    ("Land & Revenue", "Record correction", "Revenue & Disaster Management"),
)


class HistoryProvider(Protocol):
    def search(
        self,
        *,
        q: Optional[str] = None,
        district: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> HistoryPage: ...


class MockHistory:
    """Seeded fake lake rows; same process, same rows."""

    def __init__(self, n_rows: int = 120, seed: int = 7) -> None:
        rng = random.Random(seed)
        base = datetime(2024, 1, 1)
        self._rows: list[HistoryItem] = []
        for i in range(n_rows):
            category, subcategory, dept = rng.choice(_CATEGORIES)
            district = rng.choice(_DISTRICTS)
            self._rows.append(
                HistoryItem(
                    ticket_no=f"CMO2024{100000 + i}",
                    created_on=base + timedelta(days=rng.randrange(500)),
                    district=district,
                    category=category,
                    subcategory=subcategory,
                    dept=dept,
                    status=rng.choice(_STATUSES),
                    office=f"Office of the Collector, {district}",
                    grievance=(
                        f"[fake row {i}] {subcategory} issue reported in "
                        f"{district} — awaiting {dept} action."
                    ),
                )
            )
        self._rows.sort(key=lambda r: r.created_on, reverse=True)

    def search(
        self,
        *,
        q: Optional[str] = None,
        district: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> HistoryPage:
        rows = self._rows
        if district:
            rows = [r for r in rows if r.district == district]
        if category:
            rows = [r for r in rows if r.category == category]
        if q:
            needle = q.lower()
            rows = [
                r
                for r in rows
                if needle in (r.grievance or "").lower()
                or needle in r.ticket_no.lower()
            ]
        return HistoryPage(
            items=rows[offset : offset + limit],
            total=len(rows),
            limit=limit,
            offset=offset,
        )


class LakeHistory:
    """Parquet-lake backed history provider.

    ``/history`` is historical-only by design: live submissions are fetched via
    ``GET /grievance/{id}`` until the lake is re-materialized.
    """

    def __init__(self, lake_dir: Optional[Path] = None) -> None:
        self._lake_dir = lake_dir

    def search(
        self,
        *,
        q: Optional[str] = None,
        district: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> HistoryPage:
        complaints = lake.lake_path("complaints", self._lake_dir)
        if not complaints.exists():
            return HistoryPage(items=[], total=0, limit=limit, offset=offset)

        where, params = _history_filters(q=q, district=district, category=category)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        columns = (
            "ticket_no, created_on, district, category, subcategory, dept, "
            "status, office, grievance"
        )
        con = lake.connect(self._lake_dir)
        try:
            total = con.execute(
                f"SELECT count(*) FROM complaints {where_sql}", params
            ).fetchone()[0]
            cursor = con.execute(
                f"""
                SELECT {columns}
                FROM complaints
                {where_sql}
                ORDER BY created_on DESC NULLS LAST, ticket_no
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            )
            names = [col[0] for col in cursor.description]
            items = [HistoryItem(**dict(zip(names, row))) for row in cursor.fetchall()]
        finally:
            con.close()
        return HistoryPage(items=items, total=total, limit=limit, offset=offset)


def _history_filters(
    *,
    q: Optional[str] = None,
    district: Optional[str] = None,
    category: Optional[str] = None,
) -> tuple[list[str], list[str]]:
    where: list[str] = []
    params: list[str] = []
    if district:
        where.append("district = ?")
        params.append(district)
    if category:
        where.append("category = ?")
        params.append(category)
    if q:
        where.append(
            "(lower(coalesce(grievance, '')) LIKE ? OR lower(ticket_no) LIKE ?)"
        )
        needle = f"%{q.casefold()}%"
        params.extend([needle, needle])
    return where, params
