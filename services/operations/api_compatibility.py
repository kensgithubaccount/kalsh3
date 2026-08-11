"""Pinned official-contract drift gate; missing evidence never passes."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ApiContractEvidence:
    openapi_sha256: str | None
    asyncapi_sha256: str | None
    changelog_sha256: str | None
    expected_openapi_sha256: str
    expected_asyncapi_sha256: str
    expected_changelog_sha256: str

    def compatible(self) -> bool:
        observed = (self.openapi_sha256, self.asyncapi_sha256, self.changelog_sha256)
        expected = (
            self.expected_openapi_sha256,
            self.expected_asyncapi_sha256,
            self.expected_changelog_sha256,
        )
        return (
            all(value is not None and len(value) == 64 for value in observed)
            and observed == expected
        )

    def blockers(self) -> tuple[str, ...]:
        names = ("OPENAPI", "ASYNCAPI", "CHANGELOG")
        observed = (self.openapi_sha256, self.asyncapi_sha256, self.changelog_sha256)
        expected = (
            self.expected_openapi_sha256,
            self.expected_asyncapi_sha256,
            self.expected_changelog_sha256,
        )
        return tuple(
            f"{name}_{'NOT_VERIFIED' if actual is None else 'DRIFT'}"
            for name, actual, baseline in zip(names, observed, expected, strict=True)
            if actual != baseline
        )
