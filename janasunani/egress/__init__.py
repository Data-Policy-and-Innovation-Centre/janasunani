"""Declared routes that may carry citizen data beyond DPIC-controlled systems.

Only :mod:`janasunani.egress.sarvam` contains an authorized-external HTTP
client.  Callers use its provider interface; they must not create their own
Sarvam clients.
"""

from .sarvam import (
    PROVIDER_REGISTRY,
    SarvamAuditContext,
    SarvamVisionAdapter,
    SqliteAuditLog,
)

__all__ = [
    "PROVIDER_REGISTRY",
    "SarvamAuditContext",
    "SarvamVisionAdapter",
    "SqliteAuditLog",
]
