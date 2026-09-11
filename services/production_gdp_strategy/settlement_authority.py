"""Research-only authority for one immutable, post-event KXGDP decision."""

from __future__ import annotations

import hashlib
import html
import http.client
import json
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from services.market_universe.domain import stable_hash
from services.market_universe.public_read import get_market_with_body
from services.production_gdp_strategy.one_decision import (
    DecisionError,
    DecisionReceipt,
    validate_decision_receipt,
)

KALSHI_ORIGIN: Final = "https://external-api.kalshi.com"
KALSHI_HOST: Final = "external-api.kalshi.com"
KALSHI_PATH_PREFIX: Final = "/trade-api/v2/markets/"
BEA_ORIGIN: Final = "https://www.bea.gov"
BEA_HOST: Final = "www.bea.gov"
MAX_RESPONSE_BYTES: Final = 8_000_000
PARSER_VERSION: Final = "d1-g3-kxgdp-settlement-authority-v2"
ZERO = Decimal("0")
ONE = Decimal("1.00")

_MARKET_CAPABILITY = object()
_BEA_CAPABILITY = object()
_EvidenceFingerprint = tuple[str, str, str, str, int, str]
_ISSUED: dict[int, tuple[object, str, _EvidenceFingerprint]] = {}


class SettlementAuthorityError(ValueError):
    """Settlement evidence or authority failed closed."""


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
        if source not in {KALSHI_ORIGIN, BEA_ORIGIN} or type(body) is not bytes or not body:
            raise SettlementAuthorityError("evidence source or exact bytes are invalid")
        if len(body) > MAX_RESPONSE_BYTES or acquired_at.tzinfo is None:
            raise SettlementAuthorityError("evidence is unbounded or timestamp is naive")
        at = acquired_at.astimezone(UTC)
        digest = hashlib.sha256(body).hexdigest()
        fingerprint = (PARSER_VERSION, source, path, digest, len(body), at.isoformat())
        identity = stable_hash(fingerprint)
        for name, value in {
            "source": source,
            "path": path,
            "raw_body": body,
            "raw_sha256": digest,
            "acquired_at": at,
            "evidence_id": identity,
        }.items():
            object.__setattr__(self, name, value)
        _ISSUED[id(self)] = (self, identity, fingerprint)


def _current_evidence_fingerprint(evidence: _Evidence) -> tuple[_EvidenceFingerprint, str]:
    """Return the identity material currently represented by issued evidence."""
    source = getattr(evidence, "source", None)
    path = getattr(evidence, "path", None)
    raw_body = getattr(evidence, "raw_body", None)
    raw_sha256 = getattr(evidence, "raw_sha256", None)
    acquired_at = getattr(evidence, "acquired_at", None)
    evidence_id = getattr(evidence, "evidence_id", None)
    if (
        source not in {KALSHI_ORIGIN, BEA_ORIGIN}
        or not isinstance(path, str)
        or type(raw_body) is not bytes
        or not raw_body
        or len(raw_body) > MAX_RESPONSE_BYTES
        or not isinstance(raw_sha256, str)
        or not isinstance(acquired_at, datetime)
        or acquired_at.tzinfo is None
        or acquired_at.utcoffset() is None
        or not isinstance(evidence_id, str)
    ):
        raise SettlementAuthorityError("evidence identity fields are malformed")
    acquired_utc = acquired_at.astimezone(UTC)
    digest = hashlib.sha256(raw_body).hexdigest()
    if digest != raw_sha256:
        raise SettlementAuthorityError("evidence bytes/hash mismatch")
    fingerprint = (PARSER_VERSION, source, path, digest, len(raw_body), acquired_utc.isoformat())
    return fingerprint, stable_hash(fingerprint)


def _validate_evidence(evidence: _Evidence, source: str) -> None:
    if type(evidence) is not _Evidence or evidence.source != source:
        raise SettlementAuthorityError("evidence type or source is not authoritative")
    issued = _ISSUED.get(id(evidence))
    if issued is None or issued[0] is not evidence:
        raise SettlementAuthorityError("evidence was reconstructed or mutated")
    current_fingerprint, current_identity = _current_evidence_fingerprint(evidence)
    if (
        current_fingerprint != issued[2]
        or current_identity != evidence.evidence_id
        or current_identity != issued[1]
    ):
        raise SettlementAuthorityError("evidence was reconstructed or mutated")


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


def _json(body: bytes) -> dict[str, object]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettlementAuthorityError("source is not valid JSON") from exc
    if not isinstance(value, dict):
        raise SettlementAuthorityError("source is not an object")
    return value


def _acquire_market(ticker: str) -> _Evidence:
    try:
        envelope, body = get_market_with_body(ticker)
    except Exception as exc:
        raise SettlementAuthorityError("fixed Kalshi acquisition failed") from exc
    path = KALSHI_PATH_PREFIX + ticker
    if envelope.get("path") != path or envelope.get("status") != 200:
        raise SettlementAuthorityError("Kalshi response path/status is not authoritative")
    return _Evidence(
        source=KALSHI_ORIGIN,
        path=path,
        body=body,
        acquired_at=_utc(envelope.get("observed_at"), "Kalshi acquired_at"),
        capability=_MARKET_CAPABILITY,
    )


def _acquire_bea(locator: str) -> _Evidence:
    if not locator.startswith(BEA_ORIGIN + "/") or "?" in locator or "#" in locator:
        raise SettlementAuthorityError("BEA locator is not reviewed first-party authority")
    path = locator.removeprefix(BEA_ORIGIN)
    if not path.startswith("/news/") or "//" in path or ".." in path:
        raise SettlementAuthorityError("BEA locator path is outside release authority")
    connection = http.client.HTTPSConnection(
        BEA_HOST, timeout=10, context=ssl.create_default_context()
    )
    acquired = datetime.now(UTC)
    try:
        connection.request(
            "GET", path, headers={"Accept": "text/html", "User-Agent": "kalsh3-d1-g3/2.0"}
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
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SettlementAuthorityError(f"{field} is malformed") from exc
    if not parsed.is_finite():
        raise SettlementAuthorityError(f"{field} is non-finite")
    return parsed


def _rule(market: dict[str, object]) -> tuple[Decimal, str, str]:
    primary = _field(market, "rules_primary")
    secondary = str(market.get("rules_secondary", ""))
    material = f"{primary}\n{secondary}"
    lowered = material.casefold()
    if not all(term in lowered for term in ("real gdp", "advance estimate", "seasonally adjusted")):
        raise SettlementAuthorityError("KXGDP rule semantics are unsupported")
    comparators = {">": "more than", "<": "less than", ">=": "at least", "<=": "at most"}
    found = [symbol for symbol, phrase in comparators.items() if phrase in lowered]
    if len(found) != 1:
        raise SettlementAuthorityError("KXGDP comparator is contradictory or unresolved")
    matches = re.findall(
        r"(?:threshold|strike|more than|less than|at least|at most)[^\d-]*(-?\d+(?:\.\d+)?)",
        lowered,
    )
    threshold = _decimal(market.get("threshold"), "threshold")
    if matches and Decimal(matches[-1]) != threshold:
        raise SettlementAuthorityError("rule threshold conflicts with market threshold")
    # The prospective fixture uses the literal word "threshold", while the
    # exchange's finalized response normally substitutes the selected number.
    # Normalize only that representation; all other rule text remains bound.
    normalized_primary = re.sub(r"(?i)\b(threshold|strike)\b", "<selected-threshold>", primary)
    normalized_primary = re.sub(
        r"(?i)((?:more|less) than|at least|at most)\s+-?\d+(?:\.\d+)?",
        r"\1 <selected-threshold>",
        normalized_primary,
    )
    normalized_secondary = secondary
    return (
        threshold,
        found[0],
        stable_hash((normalized_primary, normalized_secondary, str(threshold), found[0])),
    )


def _bound_rule_contract(decision: DecisionReceipt) -> tuple[Decimal, str, str]:
    if decision.bundle is None:
        raise SettlementAuthorityError("decision schedule binding is missing")
    before = decision.bundle.before
    after = decision.bundle.after
    before_contract = _rule(before.raw)
    after_contract = _rule(after.raw)
    if before_contract != after_contract:
        raise SettlementAuthorityError("prospective market rule semantics changed")
    if before.threshold != decision.selected_threshold:
        raise SettlementAuthorityError("selected threshold is not bound to prospective market")
    return before_contract


def _parse_market(
    evidence: _Evidence, decision: DecisionReceipt
) -> tuple[dict[str, object], Decimal, str, str]:
    _validate_evidence(evidence, KALSHI_ORIGIN)
    outer = _json(evidence.raw_body)
    market = outer.get("market")
    if not isinstance(market, dict):
        raise SettlementAuthorityError("Kalshi market object is missing")
    ticker = decision.selected_market_ticker
    event = _field(market, "event_ticker")
    if _field(market, "ticker") != ticker or event != ticker.rsplit("-", 1)[0]:
        raise SettlementAuthorityError("selected market/event identity mismatch")
    if (
        decision.bundle is None
        or decision.bundle.schedule.market_ticker != ticker
        or decision.bundle.schedule.event_ticker != event
    ):
        raise SettlementAuthorityError("decision schedule identity does not bind market")
    if (
        "target_quarter" in market
        and market["target_quarter"] != decision.bundle.schedule.target_quarter
    ):
        raise SettlementAuthorityError("market target quarter does not match schedule")
    if "settlement_edition" in market and market[
        "settlement_edition"
    ] != decision.bundle.schedule.raw.get("settlement_edition"):
        raise SettlementAuthorityError("market settlement edition does not match schedule")
    if "schedule_id" in market and market["schedule_id"] != decision.schedule_id:
        raise SettlementAuthorityError("market schedule identity does not match decision")
    if not ticker.startswith("KXGDP-") or _field(market, "market_type") != "binary":
        raise SettlementAuthorityError("market is not ordinary binary KXGDP")
    if _field(market, "status") != "finalized":
        raise SettlementAuthorityError("market is not finalized")
    result = _field(market, "result").casefold()
    if result not in {"yes", "no"}:
        raise SettlementAuthorityError("final result is not binary")
    settlement_ts = _utc(market.get("settlement_ts"), "settlement_ts")
    threshold, comparator, rule_identity = _rule(market)
    bound_threshold, bound_comparator, bound_rule_identity = _bound_rule_contract(decision)
    if (threshold, comparator, rule_identity) != (
        bound_threshold,
        bound_comparator,
        bound_rule_identity,
    ):
        raise SettlementAuthorityError("settlement market semantics redefine original decision")
    settlement_value = _decimal(market.get("settlement_value_dollars"), "settlement value")
    if settlement_value != (ONE if result == "yes" else ZERO):
        raise SettlementAuthorityError("result conflicts with settlement_value_dollars")
    expiration = market.get("expiration_value")
    if expiration is None:
        raise SettlementAuthorityError("expiration_value is missing")
    if isinstance(expiration, str) and expiration.casefold() in {"yes", "no"}:
        if expiration.casefold() != result:
            raise SettlementAuthorityError("result conflicts with expiration_value")
    else:
        expiration_value = _decimal(expiration, "expiration_value")
        implied = {
            ">": expiration_value > threshold,
            "<": expiration_value < threshold,
            ">=": expiration_value >= threshold,
            "<=": expiration_value <= threshold,
        }[comparator]
        if ("yes" if implied else "no") != result:
            raise SettlementAuthorityError("result conflicts with expiration underlying value")
    parsed = dict(market)
    parsed["settlement_ts_parsed"] = settlement_ts
    parsed["settlement_value_parsed"] = settlement_value
    parsed["result_parsed"] = result
    return parsed, threshold, comparator, rule_identity


def _parse_bea(evidence: _Evidence, quarter: str) -> tuple[str, Decimal, date, str]:
    _validate_evidence(evidence, BEA_ORIGIN)
    try:
        text = html.unescape(evidence.raw_body.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as exc:
        raise SettlementAuthorityError("BEA response is not UTF-8") from exc
    year, number = quarter.split("-Q")
    ordinal = ("first", "second", "third", "fourth")[int(number) - 1]
    title = re.search(
        rf"Gross Domestic Product[^<\n]*{ordinal}\s+quarter[^<\n]*{year}[^<\n]*Advance Estimate",
        text,
        re.IGNORECASE,
    )
    if title is None:
        raise SettlementAuthorityError("exact current-quarter Advance Estimate section missing")
    section = text[title.start() :]
    heading = re.search(r"<h[1-6]\b|\n\s*Gross Domestic Product", section[1:], re.IGNORECASE)
    if heading:
        section = section[: heading.start() + 1]
    matches = list(
        re.finditer(
            r"Real\s+gross\s+domestic\s+product\s*\(GDP\).*?"
            r"\b(increased|decreased)\b.*?annual rate of\s+"
            r"(-?\d+(?:\.\d+)?)\s*percent",
            section,
            re.IGNORECASE | re.DOTALL,
        )
    )
    if len(matches) != 1:
        raise SettlementAuthorityError(
            "current-quarter GDP direction/value is missing or ambiguous"
        )
    match = matches[0]
    if re.search(r"increased.*decreased|decreased.*increased", match.group(0), re.IGNORECASE):
        raise SettlementAuthorityError("GDP direction text is contradictory")
    raw_value = _decimal(match.group(2), "BEA real GDP value")
    value = -abs(raw_value) if match.group(1).casefold() == "decreased" else abs(raw_value)
    release = re.search(
        r"(?:released|release date|embargoed until release)[^<\n]*?"
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        text,
        re.IGNORECASE,
    )
    if release is None:
        raise SettlementAuthorityError("BEA release date is missing")
    try:
        release_date = datetime.strptime(release.group(1), "%B %d, %Y").date()
    except ValueError as exc:
        raise SettlementAuthorityError("BEA release date is malformed") from exc
    return (
        "BEA Advance Estimate",
        value,
        release_date,
        stable_hash((section, release_date.isoformat())),
    )


@dataclass(frozen=True, slots=True)
class SettlementAuthority:
    original_decision_id: str
    original_decision_hash: str
    market_ticker: str
    event_ticker: str
    series_ticker: str
    schedule_identity: str
    event_rule_identity: str
    market_evidence_id: str
    market_raw_sha256: str
    rules_primary: str
    rules_secondary: str
    kalshi_status: str
    kalshi_result: str
    kalshi_settlement_ts: datetime
    kalshi_settlement_value_dollars: Decimal
    expiration_value: str
    updated_time: datetime | None
    bea_evidence_id: str
    bea_raw_sha256: str
    bea_target_quarter: str
    bea_edition: str
    bea_real_gdp_value: Decimal
    bea_release_date: date
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


def acquire_settlement_authority(decision: DecisionReceipt) -> SettlementAuthority:
    """Acquire finality and BEA outcome for the exact immutable decision binding."""
    try:
        validate_decision_receipt(decision)
    except DecisionError as exc:
        raise SettlementAuthorityError("original decision is not issuer-issued") from exc
    if decision.bundle is None or decision.classification.value == "EVIDENCE_INCOMPLETE":
        raise SettlementAuthorityError("original decision lacks complete schedule binding")
    schedule = decision.bundle.schedule
    quarter = schedule.target_quarter
    locator = schedule.raw.get("bea_release_locator")
    if not isinstance(locator, str) or not locator:
        raise SettlementAuthorityError("reviewed exact BEA release locator is missing")
    market_evidence = _acquire_market(decision.selected_market_ticker)
    market, threshold, comparator, rule_identity = _parse_market(market_evidence, decision)
    bea_evidence = _acquire_bea(locator)
    edition, value, release_date, _bea_section_id = _parse_bea(bea_evidence, quarter)
    if edition != schedule.raw.get("settlement_edition"):
        raise SettlementAuthorityError("BEA settlement edition does not match schedule")
    if release_date != schedule.release_at.date():
        raise SettlementAuthorityError("BEA publication date conflicts with schedule release")
    implied = {
        ">": value > threshold,
        "<": value < threshold,
        ">=": value >= threshold,
        "<=": value <= threshold,
    }[comparator]
    kalshi_result = market["result_parsed"]
    settlement_ts = market["settlement_ts_parsed"]
    if not isinstance(settlement_ts, datetime) or not isinstance(kalshi_result, str):
        raise SettlementAuthorityError("parsed Kalshi authority contract failed")
    if (
        bea_evidence.acquired_at < schedule.release_at
        or market_evidence.acquired_at < schedule.release_at
        or market_evidence.acquired_at < settlement_ts
    ):
        raise SettlementAuthorityError(
            "acquisition or settlement chronology precedes authoritative outcome"
        )
    if not decision.decision_timestamp < schedule.release_at <= settlement_ts:
        raise SettlementAuthorityError(
            "settlement chronology does not cross exact schedule release"
        )
    updated = _utc(market["updated_time"], "updated_time") if market.get("updated_time") else None
    if updated is not None and updated < settlement_ts:
        raise SettlementAuthorityError("updated_time precedes settlement")
    reconciled = implied == (kalshi_result == "yes")
    event_ticker = _field(market, "event_ticker")
    rules_primary = _field(market, "rules_primary")
    settlement_value = market["settlement_value_parsed"]
    if not isinstance(settlement_value, Decimal):
        raise SettlementAuthorityError("settlement value parser contract failed")
    return SettlementAuthority(
        original_decision_id=decision.decision_id,
        original_decision_hash=decision.payload_hash,
        market_ticker=decision.selected_market_ticker,
        event_ticker=event_ticker,
        series_ticker=str(market.get("series_ticker", "KXGDP")),
        schedule_identity=decision.schedule_id,
        event_rule_identity=rule_identity,
        market_evidence_id=market_evidence.evidence_id,
        market_raw_sha256=market_evidence.raw_sha256,
        rules_primary=rules_primary,
        rules_secondary=str(market.get("rules_secondary", "")),
        kalshi_status="finalized",
        kalshi_result=kalshi_result,
        kalshi_settlement_ts=settlement_ts,
        kalshi_settlement_value_dollars=settlement_value,
        expiration_value=str(market["expiration_value"]),
        updated_time=updated,
        bea_evidence_id=bea_evidence.evidence_id,
        bea_raw_sha256=bea_evidence.raw_sha256,
        bea_target_quarter=quarter,
        bea_edition=edition,
        bea_real_gdp_value=value,
        bea_release_date=release_date,
        threshold=threshold,
        comparator=comparator,
        bea_implied_result="yes" if implied else "no",
        reconciled=reconciled,
        finality_state=FinalityState.FINALIZED,
        authority_state=AuthorityState.COMPLETE_SETTLEMENT_AUTHORITY
        if reconciled
        else AuthorityState.OUTCOME_EVIDENCE_INCOMPLETE,
        market_acquired_at=market_evidence.acquired_at,
        bea_acquired_at=bea_evidence.acquired_at,
        parser_version=PARSER_VERSION,
        research_only=True,
        production_influence=ZERO,
    )


__all__ = [
    "AuthorityState",
    "FinalityState",
    "SettlementAuthority",
    "SettlementAuthorityError",
    "acquire_settlement_authority",
]
