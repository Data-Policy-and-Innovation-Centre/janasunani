"""Ingestion layer: Janasunani API client, Pydantic schemas, and the document
ingestion pipeline that downloads complaint documents to the S3 documents bucket.

``STATUS`` and ``OFFICE`` are reference maps for the Janasunani complaint API
(numeric status/office codes → human-readable names), used by the schemas and
the orchestrator.
"""

STATUS = {0: "To be assigned", 1: "Assigned but pending", 2: "Disposed"}

OFFICE = {
    1: "Office of Chief Minister",
    2: "Governor",
    3: "Chief Secretary",
    4: "DG & IG Police",
    5: "Departments",
    6: "Collector",
    7: "Superintendent of Police",
}
