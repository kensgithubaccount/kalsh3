"""Environment-explicit application read-only credential contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class ReadCredentialError(ValueError):
    """A credential does not satisfy the exact application read boundary."""


class ReadEnvironment(StrEnum):
    DEMO = "demo"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True, repr=False)
class ExactReadCredential:
    environment: ReadEnvironment
    key_id: str = field(repr=False)
    private_key_pem: bytes = field(repr=False)
    scopes: frozenset[str] = field(default=frozenset({"read"}), repr=False)

    def __post_init__(self) -> None:
        if self.scopes != frozenset({"read"}) or not self.key_id or not self.private_key_pem:
            raise ReadCredentialError("an exact-read credential is required")


class ExactReadCredentialProvider(Protocol):
    def resolve(self, environment: ReadEnvironment) -> ExactReadCredential: ...
