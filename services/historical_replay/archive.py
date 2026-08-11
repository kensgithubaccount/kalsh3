"""Immutable acquisition lineage for reproducible historical normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256


def stable_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    manifest_id: str
    provider: str
    endpoint: str
    request_parameters: tuple[tuple[str, str], ...]
    retrieved_at: datetime
    page_number: int
    cursor_in: str | None
    cursor_out: str | None
    record_count: int
    raw_content_hash: str
    normalized_content_hash: str
    parser_version: str
    spec_version: str
    succeeded: bool
    failure: str | None
    archive_location: str | None

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        endpoint: str,
        parameters: dict[str, str],
        retrieved_at: datetime,
        page_number: int,
        cursor_in: str | None,
        cursor_out: str | None,
        raw: bytes,
        normalized: object,
        parser_version: str,
        spec_version: str,
        succeeded: bool = True,
        failure: str | None = None,
        archive_location: str | None = None,
    ) -> ArchiveManifest:
        raw_hash = sha256(raw).hexdigest()
        normalized_hash = stable_hash(normalized)
        identity = stable_hash(
            (
                provider,
                endpoint,
                sorted(parameters.items()),
                retrieved_at,
                page_number,
                cursor_in,
                cursor_out,
                raw_hash,
                normalized_hash,
            )
        )
        return cls(
            identity,
            provider,
            endpoint,
            tuple(sorted(parameters.items())),
            retrieved_at,
            page_number,
            cursor_in,
            cursor_out,
            len(normalized) if isinstance(normalized, list) else 1,
            raw_hash,
            normalized_hash,
            parser_version,
            spec_version,
            succeeded,
            failure,
            archive_location,
        )
