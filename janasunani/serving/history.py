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
from typing import Optional, Protocol

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
