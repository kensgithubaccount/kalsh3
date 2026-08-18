"""Neutral, shared validation for M27F candidate-authority attestation artifacts.

Both ``services.supervised_canary`` (M27F live read acceptance) and
``services.production_execution`` (M27G protected write-credential enrollment) must
independently re-validate a serialized candidate-authority attestation artifact against the
exact same live policy before trusting it. ``services.supervised_canary`` already depends on
``services.production_execution.credentials`` for the write-candidate domain constants
(``REQUIRED_LIVE_WRITE_SCOPES`` / ``REQUIRED_LIVE_WRITE_SUBACCOUNT``), so
``services.production_execution`` must never depend back on ``services.supervised_canary`` --
that would create a layering cycle. This module has no dependency on either package; both
depend on it, exactly like the rest of ``services.kalshi_account_gateway`` (already the shared
foundation for Kalshi authentication/read concerns both packages build on).

Callers supply their own ``expected_scopes``/``expected_subaccount`` (both packages source
these from :mod:`services.production_execution.credentials`, so behavior is identical) rather
than this module importing those constants itself -- that keeps this module fully neutral.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from .production_read_credentials import API_KEYS_PATH, PRODUCTION_ORIGIN

# Full current official Kalshi API-key scope enum (docs.kalshi.com/api-reference/api-keys):
# broad read/write parents plus the independently grantable child scopes. Any scope string
# outside this set on a server-reported key is treated as unknown/untrusted, not silently
# accepted.
KNOWN_KALSHI_API_KEY_SCOPES: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "read::block_trade_accept",
        "read::portfolio_balance",
        "write::trade",
        "write::transfer",
        "write::block_trade_accept",
    }
)

CANDIDATE_AUTHORITY_ATTESTATION_SCHEMA = "kalsh3.m27f.candidate-authority.v1"

# Shared 30-second freshness bound for authenticated user-data evidence, reused by both the
# M27F live-read acceptance sweep and M27G's independent re-check of that evidence at
# installation time.
USER_DATA_FRESHNESS = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class AttestationValidation:
    """Independent, structural re-validation of a stored attestation artifact.

    Never merely trusts an artifact's own ``classification`` field -- every field the
    artifact claims is re-checked here against the exact live policy the caller supplies.
    """

    classification: str
    server_scopes: tuple[str, ...] | None
    server_subaccount: int | None
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def _invalid(
    reason: str,
    *,
    scopes: tuple[str, ...] | None = None,
    subaccount: int | None = None,
) -> AttestationValidation:
    return AttestationValidation("FAIL", scopes, subaccount, reason)


def validate_candidate_authority_attestation(
    payload: object,
    *,
    candidate_key_id: str,
    expected_scopes: frozenset[str],
    expected_subaccount: int,
) -> AttestationValidation:
    """Require every field of a candidate-authority attestation before trusting it.

    Fails closed on: wrong/missing schema, non-``PRODUCTION`` environment, wrong source
    origin/path, a key-ID-hash mismatch against ``candidate_key_id``, malformed or non-exact
    scopes/subaccount, any ``unique_matches`` other than exactly 1, and a ``classification``
    other than ``"PASS"``. No time-based expiry is applied here -- an attestation's validity
    is scoped to the exact candidate key ID hash it names, not to a TTL.
    """
    if not isinstance(payload, dict):
        return _invalid("authority attestation malformed")
    if payload.get("schema") != CANDIDATE_AUTHORITY_ATTESTATION_SCHEMA:
        return _invalid("authority attestation schema mismatch")
    if not isinstance(payload.get("software_version"), str) or not payload["software_version"]:
        return _invalid("authority attestation malformed")
    if payload.get("environment") != "PRODUCTION":
        return _invalid("authority attestation environment mismatch")
    if not isinstance(payload.get("observed_at"), str) or not payload["observed_at"]:
        return _invalid("authority attestation malformed")
    source = payload.get("source")
    if (
        not isinstance(source, dict)
        or source.get("origin") != PRODUCTION_ORIGIN
        or source.get("path") != API_KEYS_PATH
    ):
        return _invalid("authority attestation source mismatch")
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        return _invalid("authority attestation candidate metadata malformed")
    key_id_hash = candidate.get("key_id_hash")
    expected_hash = hashlib.sha256(candidate_key_id.encode()).hexdigest()
    if not isinstance(key_id_hash, str) or key_id_hash != expected_hash:
        return _invalid("authority attestation candidate key id mismatch")
    raw_scopes = candidate.get("server_scopes")
    if (
        not isinstance(raw_scopes, list)
        or not raw_scopes
        or any(type(item) is not str for item in raw_scopes)
        or len(raw_scopes) != len(set(raw_scopes))
    ):
        return _invalid("authority attestation scopes malformed")
    scopes = tuple(sorted(raw_scopes))
    subaccount = candidate.get("server_subaccount")
    if subaccount is not None and (type(subaccount) is bool or type(subaccount) is not int):
        return _invalid("authority attestation subaccount malformed", scopes=scopes)
    unique_matches = candidate.get("unique_matches")
    if type(unique_matches) is bool or not isinstance(unique_matches, int):
        return _invalid(
            "authority attestation match count malformed", scopes=scopes, subaccount=subaccount
        )
    if payload.get("classification") != "PASS":
        return _invalid("authority attestation did not pass", scopes=scopes, subaccount=subaccount)
    if frozenset(raw_scopes) != expected_scopes:
        return _invalid(
            "authority attestation scopes are not exactly the required write-candidate scopes",
            scopes=scopes,
            subaccount=subaccount,
        )
    if subaccount != expected_subaccount:
        return _invalid(
            "authority attestation subaccount is not the required write-candidate subaccount",
            scopes=scopes,
            subaccount=subaccount,
        )
    if unique_matches != 1:
        return _invalid(
            "authority attestation match count is not exactly one",
            scopes=scopes,
            subaccount=subaccount,
        )
    return AttestationValidation("PASS", scopes, subaccount, None)
