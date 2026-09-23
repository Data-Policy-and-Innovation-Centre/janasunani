"""Validated, aggregate-only monitoring artifact provider."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from janasunani.analytics.monitoring import MAX_ARTIFACT_BYTES
from janasunani.serving.schemas import (
    MONITORING_PANEL_IDS,
    MonitoringCatalog,
    MonitoringDashboard,
)

_FORBIDDEN_KEYS = {
    "grievance", "ticket_no", "ticketNo", "mobile", "email", "petitioner_name",
    "action_taken_by", "identity_key", "signature", "hash",
}


class MonitoringArtifactError(ValueError):
    pass


class MonitoringProvider(Protocol):
    def catalog(self) -> MonitoringCatalog: ...
    def dashboard(self, scope_id: str, period: str) -> MonitoringDashboard: ...


def _check_payload(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _check_payload(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in _FORBIDDEN_KEYS:
                raise MonitoringArtifactError(f"forbidden row-level field: {key}")
            _check_payload(item)


class ArtifactMonitoringProvider:
    def __init__(self, artifact: Path):
        self.artifact = artifact.resolve()
        if "data" in self.artifact.parts:
            raise MonitoringArtifactError("monitoring artifact must not be served from data/")

    def _release(self) -> dict:
        if not self.artifact.is_file():
            raise MonitoringArtifactError("validated monitoring artifact is unavailable")
        if self.artifact.stat().st_size > MAX_ARTIFACT_BYTES:
            raise MonitoringArtifactError("monitoring artifact exceeds the 10 MB limit")
        try:
            value = json.loads(self.artifact.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise MonitoringArtifactError("monitoring artifact is malformed") from exc
        if not isinstance(value, dict):
            raise MonitoringArtifactError("monitoring artifact root must be an object")
        _check_payload(value)
        required = {"schemaVersion", "generatedAt", "sourceFreshness", "periods", "scopes", "dashboards"}
        if not required.issubset(value):
            raise MonitoringArtifactError("monitoring artifact is incomplete")
        return value

    def catalog(self) -> MonitoringCatalog:
        release = self._release()
        try:
            return MonitoringCatalog.model_validate({
                "schemaVersion": release["schemaVersion"],
                "sourceFreshness": release["sourceFreshness"],
                "periods": release["periods"],
                "scopes": release["scopes"],
            })
        except ValidationError as exc:
            raise MonitoringArtifactError("monitoring catalog failed validation") from exc

    def dashboard(self, scope_id: str, period: str) -> MonitoringDashboard:
        release = self._release()
        catalog = self.catalog()
        scope = next((item for item in catalog.scopes if item.id == scope_id), None)
        if scope is None:
            raise KeyError("unknown monitoring scope")
        if period not in scope.available_periods:
            raise KeyError("period is not published for this scope")
        raw = release["dashboards"].get(f"{scope_id}:{period}")
        if not isinstance(raw, dict):
            raise MonitoringArtifactError("published dashboard is missing")
        try:
            result = MonitoringDashboard.model_validate({
                "schemaVersion": release["schemaVersion"],
                "generatedAt": release["generatedAt"],
                "sourceFreshness": release["sourceFreshness"],
                "artifact": self.artifact.name,
                **raw,
            })
        except ValidationError as exc:
            raise MonitoringArtifactError("monitoring dashboard failed validation") from exc
        if len(result.panels) != len(MONITORING_PANEL_IDS) or {
            panel.id for panel in result.panels
        } != set(MONITORING_PANEL_IDS):
            raise MonitoringArtifactError("monitoring dashboard must contain every governed panel")
        return result


def monitoring_provider_from_env() -> ArtifactMonitoringProvider:
    path = Path(os.environ.get(
        "JANASUNANI_MONITORING_ARTIFACT",
        "outputs/monitoring/monitoring_dashboard_v1.json",
    ))
    return ArtifactMonitoringProvider(path)
