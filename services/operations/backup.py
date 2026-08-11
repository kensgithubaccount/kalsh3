"""Content-addressed backup manifests and restore-drill verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    component: str
    object_name: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.component or not self.object_name or self.size_bytes < 0:
            raise ValueError("complete backup artifact metadata required")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ValueError("artifact requires lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class BackupManifest:
    backup_id: str
    created_at: datetime
    schema_version: str
    artifacts: tuple[BackupArtifact, ...]
    encrypted: bool
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or not self.encrypted or not self.artifacts:
            raise ValueError("backup must be timestamped, encrypted, and non-empty")
        components = [artifact.component for artifact in self.artifacts]
        if len(components) != len(set(components)):
            raise ValueError("one artifact per component is required")
        payload = json.dumps(
            {
                "backup_id": self.backup_id,
                "created_at": self.created_at.astimezone(UTC).isoformat(),
                "schema_version": self.schema_version,
                "artifacts": [
                    [item.component, item.object_name, item.size_bytes, item.sha256]
                    for item in self.artifacts
                ],
                "encrypted": self.encrypted,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "content_hash", hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class RestoreDrill:
    drill_id: str
    manifest_hash: str
    performed_at: datetime
    isolated_target: bool
    checksums_verified: bool
    migrations_verified: bool
    row_counts_verified: bool
    application_smoke_verified: bool
    production_network_blocked: bool

    def __post_init__(self) -> None:
        if self.performed_at.tzinfo is None:
            raise ValueError("restore drill timestamp must be timezone aware")
        if len(self.manifest_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.manifest_hash
        ):
            raise ValueError("restore drill requires manifest SHA-256")

    @property
    def passed(self) -> bool:
        return all(
            (
                self.isolated_target,
                self.checksums_verified,
                self.migrations_verified,
                self.row_counts_verified,
                self.application_smoke_verified,
                self.production_network_blocked,
            )
        )
