"""Research-only, post-event KXGDP settlement authority.

The only public entry point owns both fixed-source GETs.  Evidence and parsed
objects are issuer-issued through private capabilities; hashes alone never
create source authority.  This module does not know about accounts, orders,
fees, clocks, stores, or the D1-G2 evaluator.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from services.market_universe.domain import stable_hash
from services.market_universe.public_read import get_market_with_body

KALSHI_ORIGIN: Final = "https://external-api.kalshi.com"
KALSHI_HOST: Final = "external-api.kalshi.com"
KALSHI_PATH_PREFIX: Final = "/trade-api/v2/markets/"
BEA_ORIGIN: Final = "https://www.bea.gov"
BEA_HOST: Final = "www.bea.gov"
MAX_RESPONSE_BYTES: Final = 8_000_000
PARSER_VERSION: Final = "d1-g3-kxgdp-settlement-authority-v1"
ZERO = Decimal("0")
ONE = Decimal("1.00")

_MARKET_CAPABILITY = object()
_BEA_CAPABILITY = object()
_ISSUED_MARKETS: dict[int, tuple[object, str]] = {}
_ISSUED_BEA: dict[int, tuple[object, str]] = {}


class SettlementAuthorityError(ValueError):
    """A settlement evidence or authority invariant failed closed."""


class AuthorityState(StrEnum):
    COMPLETE_SETTLEMENT_AUTHORITY = "COMPLETE SETTLEMENT AUTHORITY"
    OUTCOME_EVIDENCE_INCOMPLETE = "OUTCOME EVIDENCE_INCOMPLETE"


class FinalityState(StrEnum):
    FINALIZED = "FINALIZED"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


@dataclass(frozen=True, slots=True, init=False)
class _Evidence:
    source: str
    path: str
    raw_body: bytes
    raw_sha256: str
    acquired_at: datetime
    evidence_id: str

    def __init__(
        self, *, source: str, path: str, body: bytes, acquired_at: datetime, capability: object
    ) -> None:
        if capability not in {_MARKET_CAPABILITY, _BEA_CAPABILITY}:
            raise SettlementAuthorityError("evidence requires reviewed acquisition")
        if source not in {KALSHI_ORIGIN, BEA_ORIGIN} or not isinstance(body, bytes) or not body:
            raise SettlementAuthorityError("evidence source or exact bytes are invalid")
        if len(body) > MAX_RESPONSE_BYTES or acquired_at.tzinfo is None:
            raise SettlementAuthorityError("evidence is unbounded or timestamp is naive")
        at = acquired_at.astimezone(UTC)
        digest = hashlib.sha256(body).hexdigest()
        identity = stable_hash((PARSER_VERSION, source, path, digest, len(body), at.isoformat()))
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "raw_body", body)
        object.__setattr__(self, "raw_sha256", digest)
        object.__setattr__(self, "acquired_at", at)
        object.__setattr__(self, "evidence_id", identity)
        (_ISSUED_MARKETS if capability is _MARKET_CAPABILITY else _ISSUED_BEA)[id(self)] = (
            self,
            identity,
        )


def _validate_evidence(evidence: _Evidence, capability: object, source: str) -> None:
    if type(evidence) is not _Evidence or evidence.source != source:
        raise SettlementAuthorityError("evidence type or source is not authoritative")
    registry = _ISSUED_MARKETS if capability is _MARKET_CAPABILITY else _ISSUED_BEA
    issued = registry.get(id(evidence))
    if issued is None or issued[0] is not evidence or issued[1] != evidence.evidence_id:
        raise SettlementAuthorityError("evidence was reconstructed or mutated")
    if hashlib.sha256(evidence.raw_body).hexdigest() != evidence.raw_sha256:
        raise SettlementAuthorityError("evidence bytes/hash mismatch")


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise SettlementAuthorityError(f"{field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SettlementAuthorityError(f"{field} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SettlementAuthorityError(f"{field} has no timezone")
    return parsed.astimezone(UTC)


def _json(body: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettlementAuthorityError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SettlementAuthorityError(f"{label} is not an object")
    return value


def _acquire_market(ticker: str) -> _Evidence:
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,127}", ticker):
        raise SettlementAuthorityError("market ticker is malformed")
    try:
        envelope, body = get_market_with_body(ticker)
    except Exception as exc:
        raise SettlementAuthorityError("fixed Kalshi acquisition failed") from exc
    expected = KALSHI_PATH_PREFIX + ticker
    if envelope.get("path") != expected or envelope.get("status") != 200:
        raise SettlementAuthorityError("Kalshi response path/status is not authoritative")
    return _Evidence(
        source=KALSHI_ORIGIN,
        path=expected,
        body=body,
        acquired_at=_utc(envelope.get("observed_at"), "Kalshi acquired_at"),
        capability=_MARKET_CAPABILITY,
    )


def _bea_path(quarter: str) -> str:
    match = re.fullmatch(r"(\d{4})-Q([1-4])", quarter)
    if not match:
        raise SettlementAuthorityError("target quarter is malformed")
    ordinal = ("first", "second", "third", "fourth")[int(match.group(2)) - 1]
    year = match.group(1)
    return f"/news/{year}/gross-domestic-product-{ordinal}-quarter-{year}-advance-estimate"


def _acquire_bea(quarter: str) -> _Evidence:
    path = _bea_path(quarter)
    connection = http.client.HTTPSConnection(
        BEA_HOST, timeout=10, context=ssl.create_default_context()
    )
    acquired = datetime.now(UTC)
    try:
        connection.request(
            "GET", path, headers={"Accept": "text/html", "User-Agent": "kalsh3-d1-g3/1.0"}
        )
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(body) > MAX_RESPONSE_BYTES:
            raise SettlementAuthorityError("BEA response is not bounded successful evidence")
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise SettlementAuthorityError("fixed BEA acquisition failed") from exc
    finally:
        connection.close()
    return _Evidence(
        source=BEA_ORIGIN, path=path, body=body, acquired_at=acquired, capability=_BEA_CAPABILITY
    )


def _field(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise SettlementAuthorityError(f"market field {name} is missing or malformed")
    return value


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise SettlementAuthorityError(f"{field} is missing or malformed")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SettlementAuthorityError(f"{field} is malformed") from exc
    if not result.is_finite():
        raise SettlementAuthorityError(f"{field} is non-finite")
    return result


def _parse_market(evidence: _Evidence, ticker: str) -> tuple[dict[str, object], Decimal, str]:
    _validate_evidence(evidence, _MARKET_CAPABILITY, KALSHI_ORIGIN)
    outer = _json(evidence.raw_body, "Kalshi market")
    market = outer.get("market")
    if not isinstance(market, dict):
        raise SettlementAuthorityError("Kalshi market object is missing")
    event_ticker = _field(market, "event_ticker")
    expected_event = ticker.rsplit("-", 1)[0]
    if _field(market, "ticker") != ticker or event_ticker != expected_event:
        raise SettlementAuthorityError("selected market identity mismatch")
    if _field(market, "ticker").split("-", 1)[0] != "KXGDP":
        raise SettlementAuthorityError("selected market is not KXGDP")
    if _field(market, "market_type") != "binary":
        raise SettlementAuthorityError("ordinary binary payout semantics are unresolved")
    status = _field(market, "status")
    if status != "finalized":
        raise SettlementAuthorityError("market is not Kalshi-finalized")
    result = _field(market, "result").lower()
    if result not in {"yes", "no"}:
        raise SettlementAuthorityError("Kalshi final result is not binary")
    settlement_ts = _utc(market.get("settlement_ts"), "settlement_ts")
    rule = (_field(market, "rules_primary") + " " + str(market.get("rules_secondary", ""))).lower()
    if (
        "real gdp" not in rule
        or "advance estimate" not in rule
        or "seasonally adjusted" not in rule
    ):
        raise SettlementAuthorityError("KXGDP contract rule is not positively identified")
    if "more than" in rule:
        comparator = ">"
    elif "less than" in rule:
        comparator = "<"
    elif "at least" in rule:
        comparator = ">="
    elif "at most" in rule:
        comparator = "<="
    else:
        raise SettlementAuthorityError("KXGDP comparator is unresolved")
    threshold = _decimal(market.get("threshold"), "threshold")
    parsed = dict(market)
    parsed["settlement_ts_parsed"] = settlement_ts
    return parsed, threshold, comparator


def _parse_bea(evidence: _Evidence, quarter: str) -> tuple[str, Decimal, datetime, str]:
    _validate_evidence(evidence, _BEA_CAPABILITY, BEA_ORIGIN)
    text = evidence.raw_body.decode("utf-8", errors="strict")
    year, q_number = quarter.split("-Q")
    ordinal = ("first", "second", "third", "fourth")[int(q_number) - 1]
    quarter_present = re.search(
        rf"(?:{ordinal}\s+quarter\s+{year}|Q{q_number}\s+{year})", text, re.IGNORECASE
    )
    if "Advance Estimate" not in text or "Real GDP" not in text or quarter_present is None:
        raise SettlementAuthorityError("BEA artifact is not the exact Advance Estimate")
    if any(term in text for term in ("Second Estimate", "Third Estimate", "revised")):
        raise SettlementAuthorityError("BEA artifact is a later or revised estimate")
    patterns = (
        r"Real GDP[^%\d-]*(-?\d+(?:\.\d+)?)\s*percent",
        r"real GDP[^%\d-]*(-?\d+(?:\.\d+)?)",
    )
    match = next((re.search(pattern, text, re.IGNORECASE) for pattern in patterns), None)
    if match is None:
        raise SettlementAuthorityError("BEA real GDP value is missing")
    value = _decimal(match.group(1), "BEA real GDP value")
    stamp = re.search(
        r"(?:Release|released)[^0-9]*(\d{4}-\d{2}-\d{2})|"
        r"(?:Release|released)[^A-Za-z]*(?:at\s+[^,]+,\s+)?"
        r"([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        text,
        re.IGNORECASE,
    )
    if stamp is None:
        raise SettlementAuthorityError("BEA release timestamp is missing")
    release_value = stamp.group(1) or stamp.group(2)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", release_value):
        release_ts = _utc(release_value + "T00:00:00+00:00", "BEA release timestamp")
    else:
        try:
            release_ts = datetime.strptime(release_value, "%B %d, %Y").replace(tzinfo=UTC)
        except ValueError as exc:
            raise SettlementAuthorityError("BEA release timestamp is malformed") from exc
    edition = "BEA Advance Estimate"
    return edition, value, release_ts, text


@dataclass(frozen=True, slots=True)
class SettlementAuthority:
    market_ticker: str
    event_ticker: str
    series_ticker: str
    title: str
    subtitle: str
    rules_primary: str
    rules_secondary: str
    market_evidence_id: str
    market_raw_sha256: str
    event_rule_identity: str
    kalshi_status: str
    kalshi_result: str
    kalshi_settlement_ts: datetime
    kalshi_settlement_value_dollars: Decimal | None
    expiration_value: str | None
    updated_time: datetime | None
    bea_evidence_id: str
    bea_raw_sha256: str
    bea_target_quarter: str
    bea_edition: str
    bea_real_gdp_value: Decimal
    bea_release_ts: datetime
    threshold: Decimal
    comparator: str
    bea_implied_result: str
    reconciled: bool
    finality_state: FinalityState
    authority_state: AuthorityState
    market_acquired_at: datetime
    bea_acquired_at: datetime
    parser_version: str
    research_only: bool
    production_influence: Decimal

    @property
    def winning_selected_contract_payout(self) -> Decimal:
        return ONE

    @property
    def losing_selected_contract_payout(self) -> Decimal:
        return ZERO


def acquire_settlement_authority(market_ticker: str, target_quarter: str) -> SettlementAuthority:
    """Acquire and reconcile one exact KXGDP market and its BEA Advance Estimate."""
    market_evidence = _acquire_market(market_ticker)
    market, threshold, comparator = _parse_market(market_evidence, market_ticker)
    settlement_ts = market["settlement_ts_parsed"]
    if not isinstance(settlement_ts, datetime):
        raise SettlementAuthorityError("settlement timestamp parser contract failed")
    bea_evidence = _acquire_bea(target_quarter)
    edition, value, release_ts, _ = _parse_bea(bea_evidence, target_quarter)
    implied = {
        ">": value > threshold,
        "<": value < threshold,
        ">=": value >= threshold,
        "<=": value <= threshold,
    }[comparator]
    kalshi_result = str(market["result"]).lower()
    authority = SettlementAuthority(
        market_ticker=market_ticker,
        event_ticker=_field(market, "event_ticker"),
        series_ticker=str(market.get("series_ticker", "KXGDP")),
        title=str(market.get("title", "")),
        subtitle=str(market.get("subtitle", "")),
        rules_primary=_field(market, "rules_primary"),
        rules_secondary=str(market.get("rules_secondary", "")),
        market_evidence_id=market_evidence.evidence_id,
        market_raw_sha256=market_evidence.raw_sha256,
        event_rule_identity=stable_hash(
            (market.get("event_ticker"), market.get("rules_primary"), market.get("rules_secondary"))
        ),
        kalshi_status="finalized",
        kalshi_result=kalshi_result,
        kalshi_settlement_ts=settlement_ts,
        kalshi_settlement_value_dollars=_decimal(
            market["settlement_value_dollars"], "settlement value"
        )
        if market.get("settlement_value_dollars") is not None
        else None,
        expiration_value=str(market["expiration_value"])
        if market.get("expiration_value") is not None
        else None,
        updated_time=_utc(market["updated_time"], "updated_time")
        if market.get("updated_time") is not None
        else None,
        bea_evidence_id=bea_evidence.evidence_id,
        bea_raw_sha256=bea_evidence.raw_sha256,
        bea_target_quarter=target_quarter,
        bea_edition=edition,
        bea_real_gdp_value=value,
        bea_release_ts=release_ts,
        threshold=threshold,
        comparator=comparator,
        bea_implied_result="yes" if implied else "no",
        reconciled=(implied == (kalshi_result == "yes")),
        finality_state=FinalityState.FINALIZED,
        authority_state=AuthorityState.COMPLETE_SETTLEMENT_AUTHORITY
        if implied == (kalshi_result == "yes")
        else AuthorityState.OUTCOME_EVIDENCE_INCOMPLETE,
        market_acquired_at=market_evidence.acquired_at,
        bea_acquired_at=bea_evidence.acquired_at,
        parser_version=PARSER_VERSION,
        research_only=True,
        production_influence=ZERO,
    )
    return authority


__all__ = [
    "AuthorityState",
    "FinalityState",
    "SettlementAuthority",
    "SettlementAuthorityError",
    "acquire_settlement_authority",
]
