"""FastAPI app — the demo API (Phase 10 skeleton).

Endpoints (the full surface; shapes in schemas.py are the frozen contract):

- ``POST /grievance``       multipart: ``text`` XOR ``file`` (+ optional
                            ``district``) -> full ``GrievanceResult``, 201
- ``GET  /grievance/{id}``  a previously submitted result
- ``GET  /history``         browse/search historical complaints (lake shape)
- ``GET  /health``          liveness + which processor is mounted

Skeleton wiring (swapped at Phase 8/9 wire-up, endpoints unchanged):
processor = ``MockGrievanceProcessor``; history = ``MockHistory``; submitted
results live in an in-process dict (wire-up persists to the ``live_grievances``
OLTP table instead — the dict, like everything mock, dies with the process).

Run:  uv run --extra serving janasunani-api          # 127.0.0.1:8000
CORS: comma-separated ``JANASUNANI_CORS_ORIGINS`` (default ``*`` — fine for
the skeleton; pin to the frontend origin at deploy).
"""

from __future__ import annotations

import functools
import itertools
import os
import uuid
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from starlette.concurrency import run_in_threadpool

from janasunani.serving.history import HistoryProvider, MockHistory
from janasunani.serving.processor import GrievanceProcessor, MockGrievanceProcessor
from janasunani.serving.schemas import (
    GrievanceResult,
    HealthResponse,
    HistoryPage,
)


def create_app(
    processor: Optional[GrievanceProcessor] = None,
    history: Optional[HistoryProvider] = None,
) -> FastAPI:
    """App factory; tests and the wire-up inject their own processor/history."""
    processor = processor or MockGrievanceProcessor()
    history = history or MockHistory()

    app = FastAPI(title="Janasunani 2.0 API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("JANASUNANI_CORS_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Skeleton-only store for GET /grievance/{id}; replaced by OLTP at wire-up.
    results: dict[str, GrievanceResult] = {}
    # next() has no await point, so concurrent submissions can't mint the same
    # number (len(results)+1 could: requests overlap at `await file.read()`).
    ticket_seq = itertools.count(1)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", processor=processor.name)

    @app.post("/grievance", response_model=GrievanceResult, status_code=201)
    async def submit_grievance(
        text: Optional[str] = Form(None),
        file: Optional[UploadFile] = File(None),
        district: Optional[str] = Form(None),
    ) -> GrievanceResult:
        if (text is None) == (file is None):
            raise HTTPException(
                status_code=422,
                detail="Provide exactly one of 'text' or 'file'.",
            )
        if text is not None and not text.strip():
            raise HTTPException(status_code=422, detail="'text' is empty.")

        grievance_id = uuid.uuid4().hex[:12]
        ticket_no = f"JS{next(ticket_seq):07d}"
        # The processor protocol is sync (real inference is CPU/GPU-bound
        # work); run it off the event loop so a slow OCR/model call doesn't
        # block /health and other requests on this worker at wire-up.
        result = await run_in_threadpool(
            functools.partial(
                processor.process,
                grievance_id=grievance_id,
                ticket_no=ticket_no,
                text=text,
                document_name=file.filename if file else None,
                document_bytes=await file.read() if file else None,
                district=district,
            )
        )
        results[grievance_id] = result
        logger.info(
            f"grievance {grievance_id} ({ticket_no}): "
            f"{result.extraction.source} via {processor.name} -> "
            f"{result.classification.category} / {result.routing.dept}"
        )
        return result

    @app.get("/grievance/{grievance_id}", response_model=GrievanceResult)
    def get_grievance(grievance_id: str) -> GrievanceResult:
        result = results.get(grievance_id)
        if result is None:
            raise HTTPException(status_code=404, detail="No such grievance.")
        return result

    @app.get("/history", response_model=HistoryPage)
    def get_history(
        q: Optional[str] = Query(None, description="substring of grievance/ticket"),
        district: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> HistoryPage:
        return history.search(
            q=q, district=district, category=category, limit=limit, offset=offset
        )

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "janasunani.serving.api:app",
        host=os.environ.get("JANASUNANI_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("JANASUNANI_API_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
