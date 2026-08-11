"""Allowlisted, bounded contract-document retrieval; retrieved content remains untrusted data."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit


class DocumentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    document_id: str
    url: str
    retrieved_at: datetime
    content_type: str
    content_hash: str
    content: bytes


class DocumentTransport(Protocol):
    def get(
        self, url: str, *, timeout_seconds: float, max_bytes: int, follow_redirects: bool
    ) -> tuple[str, bytes]: ...


@dataclass(frozen=True, slots=True)
class ContractDocumentConnector:
    transport: DocumentTransport
    allowed_hosts: frozenset[str] = frozenset({"kalshi.com", "www.kalshi.com", "docs.kalshi.com"})
    max_bytes: int = 2_000_000
    timeout_seconds: float = 10

    def retrieve(self, url: str, now: datetime) -> DocumentVersion:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.allowed_hosts
            or parsed.username
            or parsed.password
        ):
            raise DocumentError("contract URL is outside the exact HTTPS allowlist")
        content_type, content = self.transport.get(
            url,
            timeout_seconds=self.timeout_seconds,
            max_bytes=self.max_bytes,
            follow_redirects=False,
        )
        if len(content) > self.max_bytes:
            raise DocumentError("contract document exceeds size bound")
        normalized = content_type.split(";", 1)[0].lower()
        if normalized not in {"text/html", "text/plain", "application/pdf"}:
            raise DocumentError("contract content type is unsupported")
        digest = hashlib.sha256(content).hexdigest()
        return DocumentVersion(digest[:24], url, now, normalized, digest, content)
