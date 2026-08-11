"""Exact-host public research connectors; all are background read-only boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConnectorState(StrEnum):
    MOCK = "MOCK"
    SETUP_REQUIRED = "SETUP_REQUIRED"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class ReadConnectorPolicy:
    provider: str
    host: str
    paths: tuple[str, ...]
    method: str
    timeout_seconds: float
    max_payload_bytes: int
    content_types: tuple[str, ...]
    identifying_user_agent: str | None
    state: ConnectorState

    def validate(self) -> None:
        hosts = {
            "NWS": "api.weather.gov",
            "NOAA": "www.ncei.noaa.gov",
            "BLS": "api.bls.gov",
            "BEA": "apps.bea.gov",
            "FRED": "api.stlouisfed.org",
            "EIA": "api.eia.gov",
        }
        if (
            self.host != hosts.get(self.provider)
            or not self.paths
            or any(not path.startswith("/") for path in self.paths)
        ):
            raise ValueError("connector host/path is not allowlisted")
        if self.method not in {"GET", "EXTERNAL_READ_QUERY_POST"}:
            raise ValueError("connector has mutation semantics")
        if self.provider == "NWS" and not self.identifying_user_agent:
            raise ValueError("NWS identifying User-Agent required")
