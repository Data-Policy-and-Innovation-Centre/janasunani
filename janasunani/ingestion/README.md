# janasunani.ingestion — API client, S3, document download

Everything that brings data *into* the system from outside: the Janasunani API,
complaint documents, and the schemas that gatekeep both paths.

## Modules

- `schemas.py` — **the single raw-source → ORM column map.** Every `Field`
  alias is a raw source column name (dump or API); the field name is the clean
  ORM column. Both migration and API ingestion validate through these, so messy
  source names live in exactly one place. Deliberately lenient (everything but
  `ticket_no` optional; validators normalize but never raise) — a single odd
  value must not drop a historical record. Also the home of the NUL-stripping
  `mode="before"` validator (Postgres rejects `0x00` in text).
- `client.py` — Janasunani API client (`httpx`, `with_retry` backoff).
  **Status:** API credentials currently unavailable; the live-pull path is
  parked and untested against the real API.
- `s3service.py` — `S3Service` over boto3's **default credential chain**: env
  vars or `~/.aws` locally, the **instance role** on EC2. Leave the `AWS_*`
  settings unset unless a static key is genuinely needed. Reused for documents
  and (planned) MLflow artifacts.
- `document_ingestion.py` (`janasunani-ingest-documents`) — for each complaint
  with a `document_url`: download, write to S3 (or `LOCAL_STORAGE_PATH` in
  dev), and record status back onto the complaint's document-status columns in
  OLTP in bulk. Failed uploads are recorded, not silently skipped.

## Gotchas

- The documents bucket (`janasunani-documents-main`) is partly
  **GLACIER-archived** — sample/backfill selection must filter to
  STANDARD-class objects or downloads fail.
- Document keys preserve the source directory structure; the pipeline's ticket
  parsing (`janasunani/pipeline/ticket.py`) depends on paths starting at the
  `documents/` root. Don't flatten.
