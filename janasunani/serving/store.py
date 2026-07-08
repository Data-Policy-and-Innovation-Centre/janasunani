"""Submitted-result stores for ``GET /grievance/{id}``.

The API factory keeps an in-memory store available for contract tests and the
mock skeleton (the module-level app in ``api.py`` uses it by default, so the
skeleton needs no DB setup). ``DatabaseResultStore`` is available for explicit
injection (``create_app(result_store=DatabaseResultStore(...))``) so live demo
submissions survive process restarts once the Alembic migration has run.
"""

from __future__ import annotations

from typing import Optional, Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from janasunani.db import crud
from janasunani.serving.schemas import GrievanceResult


class ResultStore(Protocol):
    async def save(
        self, result: GrievanceResult, *, district: Optional[str] = None
    ) -> None: ...

    async def get(self, grievance_id: str) -> Optional[GrievanceResult]: ...


class InMemoryResultStore:
    """Process-local store used by tests and explicit mock apps."""

    def __init__(self) -> None:
        self._results: dict[str, GrievanceResult] = {}

    async def save(
        self, result: GrievanceResult, *, district: Optional[str] = None
    ) -> None:
        self._results[result.id] = result

    async def get(self, grievance_id: str) -> Optional[GrievanceResult]:
        return self._results.get(grievance_id)


class DatabaseResultStore:
    """OLTP-backed live grievance store."""

    def __init__(self, oltp_url: str) -> None:
        self._engine = create_async_engine(oltp_url)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def save(
        self, result: GrievanceResult, *, district: Optional[str] = None
    ) -> None:
        async with self._sessions() as session:
            await crud.create_or_update_live_grievance(
                session, result.model_dump(mode="json"), district=district
            )

    async def get(self, grievance_id: str) -> Optional[GrievanceResult]:
        async with self._sessions() as session:
            row = await crud.get_live_grievance_by_id(session, grievance_id)
            if row is None:
                return None
            return GrievanceResult.model_validate(row.result_json)

    async def dispose(self) -> None:
        await self._engine.dispose()
