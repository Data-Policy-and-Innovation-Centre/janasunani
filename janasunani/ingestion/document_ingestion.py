"""Download complaint documents to S3 (or local disk in dev) and record the
download status back into the OLTP store.

For each complaint with a ``document_url``, the service downloads the file and:
- in a local env (``ENV`` in ``LOCAL_ENVS``) writes it under ``LOCAL_STORAGE_PATH``;
- otherwise uploads it to the S3 documents bucket.
Results are written back to the complaint's document-status columns in OLTP
(``local_document_path`` / ``document_downloaded`` / ``document_download_date`` /
``document_download_error``) via a single bulk update per batch.
"""

import asyncio
import glob
import io
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiofiles
import httpx
import pytz
from loguru import logger
from more_itertools import chunked
from sqlalchemy import case, update
from sqlalchemy.ext.asyncio import AsyncSession
from tqdm import tqdm

from janasunani.config import settings
from janasunani.db.crud import get_complaints_without_documents
from janasunani.db.models import Complaint as ComplaintModel
from janasunani.ingestion.client import with_retry
from janasunani.ingestion.s3service import S3Service

# Environments that use local-disk storage instead of S3.
LOCAL_ENVS = {"local", "dev", "test"}
_TZ = pytz.timezone("Asia/Kolkata")
_EXT_PATTERN = re.compile(r"~([a-zA-Z]*)$")


class DocumentService:
    """Async downloader for complaint documents → S3 (or local disk in dev)."""

    def __init__(self, db: AsyncSession, s3_bucket: Optional[str] = None):
        self.db = db
        self.semaphore = asyncio.Semaphore(15)
        self.use_s3 = settings.ENV not in LOCAL_ENVS
        if self.use_s3:
            self.s3_service = S3Service(s3_bucket or settings.AWS_S3_DOCUMENTS)
        else:
            os.makedirs(settings.LOCAL_STORAGE_PATH, exist_ok=True)

    def _filename(self, ticket_no: str, document_url: str, document_type: str) -> str:
        """Build ``{ticket}_{type}_{timestamp}.{ext}`` from the document URL."""
        match = _EXT_PATTERN.search(document_url or "")
        ext = f".{match.group(1).lower()}" if match else ".bin"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{ticket_no}_{document_type}_{timestamp}{ext}"

    def _already_downloaded(
        self, ticket_no: str, document_type: str, extension: str
    ) -> bool:
        """True if a matching document already exists (any timestamp), in S3 or local."""
        if self.use_s3:
            prefix = f"{ticket_no}_{document_type}_"
            return any(
                obj["Key"].lower().endswith(f".{extension.lower()}")
                for obj in self.s3_service.list_objects(prefix=prefix)
            )
        pattern = os.path.join(
            settings.LOCAL_STORAGE_PATH, f"{ticket_no}_{document_type}_*.{extension.lower()}"
        )
        return len(glob.glob(pattern)) > 0

    @with_retry()
    async def download_document(
        self, complaint: ComplaintModel, document_type: str = "complaint"
    ) -> Optional[str]:
        """Download one complaint's document. Returns the stored path/key, or None
        if there's no valid URL or it was already downloaded. Raises on HTTP error
        (so @with_retry can retry / the caller can record the failure)."""
        url, ticket_no = complaint.document_url, complaint.ticket_no
        if not url or not url.lower().startswith(("http://", "https://")):
            logger.warning(f"Complaint {ticket_no} has no valid document URL.")
            return None

        filename = self._filename(ticket_no, url, document_type)
        extension = os.path.splitext(filename)[1][1:].lower()
        if self._already_downloaded(ticket_no, document_type, extension):
            logger.info(f"Document for complaint {ticket_no} already saved.")
            return None

        async with self.semaphore:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
                content = response.content
            if self.use_s3:
                self.s3_service.upload_fileobj(io.BytesIO(content), filename)
                stored = filename
            else:
                stored = os.path.join(settings.LOCAL_STORAGE_PATH, filename)
                async with aiofiles.open(stored, "wb") as f:
                    await f.write(content)
        logger.info(f"Downloaded document for complaint {ticket_no} -> {stored}")
        return stored

    async def batch_download_documents(
        self, complaints: List[ComplaintModel], batch_size: int = 500
    ) -> Dict[str, str]:
        """Download a list of complaints concurrently, bulk-updating OLTP status
        every ``batch_size`` results."""
        results: Dict[str, str] = {}
        batch_updates: List[Dict] = []

        async def process(complaint: ComplaintModel) -> Tuple[str, str, Dict]:
            try:
                path = await self.download_document(complaint)
                status = "success" if path else "skipped"
                return complaint.ticket_no, status, {
                    "ticket_no": complaint.ticket_no,
                    "local_path": path,
                    "success": status == "success",
                    "error": None,
                }
            except Exception as e:  # noqa: BLE001 - record per-complaint failure
                return complaint.ticket_no, "failed", {
                    "ticket_no": complaint.ticket_no,
                    "local_path": None,
                    "success": False,
                    "error": str(e),
                }

        tasks = [process(c) for c in complaints]
        for counter, coro in enumerate(
            tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Downloading documents"),
            start=1,
        ):
            ticket_no, status, update_data = await coro
            results[ticket_no] = status
            batch_updates.append(update_data)
            if counter % batch_size == 0:
                await self._bulk_update_document_status(batch_updates)
                batch_updates = []

        if batch_updates:
            await self._bulk_update_document_status(batch_updates)
        return results

    async def batch_download_documents_in_chunks(
        self, complaints: List[ComplaintModel], chunk_size: int = 100
    ) -> Dict[str, str]:
        results: Dict[str, str] = {}
        for i, chunk in enumerate(chunked(complaints, chunk_size), 1):
            logger.info(f"Processing chunk {i} ({len(chunk)} complaints)")
            results.update(await self.batch_download_documents(chunk))
        return results

    async def _bulk_update_document_status(self, updates: List[Dict]) -> None:
        """One bulk UPDATE of the document-status columns for a batch of complaints."""
        if not updates:
            return
        tickets = [u["ticket_no"] for u in updates]
        now = datetime.now(_TZ)
        stmt = (
            update(ComplaintModel)
            .where(ComplaintModel.ticket_no.in_(tickets))
            .values(
                local_document_path=case(
                    {u["ticket_no"]: u["local_path"] for u in updates},
                    value=ComplaintModel.ticket_no,
                    else_=ComplaintModel.local_document_path,
                ),
                document_downloaded=case(
                    {u["ticket_no"]: u["success"] for u in updates},
                    value=ComplaintModel.ticket_no,
                    else_=ComplaintModel.document_downloaded,
                ),
                document_download_date=case(
                    {u["ticket_no"]: (now if u["success"] else None) for u in updates},
                    value=ComplaintModel.ticket_no,
                    else_=ComplaintModel.document_download_date,
                ),
                document_download_error=case(
                    {u["ticket_no"]: u["error"] for u in updates},
                    value=ComplaintModel.ticket_no,
                    else_=ComplaintModel.document_download_error,
                ),
            )
        )
        try:
            result = await self.db.execute(stmt)
            await self.db.commit()
            logger.info(f"Bulk-updated {result.rowcount} document statuses")
        except Exception:
            await self.db.rollback()
            raise


async def run_document_ingestion(chunk_size: int = 100) -> Dict[str, str]:
    """Console-entry flow: download documents for all complaints that still need
    them, recording status in OLTP."""
    from janasunani.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        service = DocumentService(db=db)
        complaints = await get_complaints_without_documents(db)
        logger.info(f"{len(complaints)} complaints need documents")
        return await service.batch_download_documents_in_chunks(complaints, chunk_size)


def main() -> None:
    asyncio.run(run_document_ingestion())


if __name__ == "__main__":
    main()
