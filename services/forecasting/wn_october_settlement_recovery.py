"""Offline-only request planning and synthetic settlement-envelope replay.

No network adapter, CLI, callbacks, clock acquisition, persistence, scorer or
positive evidence issuer exists here. Logical replay times are not observations.
The frozen routing hints are untrusted historical archive fields, not new truth.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, cast

ORIGIN = "https://external-api.kalshi.com"
BASE = "/trade-api/v2"
ROSTER_SHA256 = "ab03a2d4c923837b4aeff3a3ca60b3145c378d114b417de1bf362ac9e28eb6d7"
MAX_REQUESTS = 85
MAX_BODY_BYTES = 65_536
MAX_HEADER_BYTES = 16_384
REQUEST_TIMEOUT_MS = 10_000
TOTAL_DEADLINE_MS = 900_000
MIN_START_INTERVAL_MS = 3_000
HEADERS = (("Accept", "application/json"), ("Accept-Encoding", "identity"))
ROSTER = (
    Path(__file__).resolve().parents[2]
    / "experiments/wn_october_settlement_recovery_v1/frozen_request_roster.json"
)


class RecoveryError(ValueError):
    """Invalid frozen metadata or simulated envelope; never source authority."""


def _require(value: bool, reason: str) -> None:
    if not value:
        raise RecoveryError(reason)


def _object(value: object) -> dict[str, object]:
    _require(type(value) is dict, "OBJECT_REQUIRED")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    _require(type(value) is str, "STRING_REQUIRED")
    return cast(str, value)


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        _require(key not in out, "DUPLICATE_JSON_KEY")
        out[key] = value
    return out


def _constant(_: str) -> object:
    raise RecoveryError("NONFINITE_JSON")


def _json(raw: bytes) -> dict[str, object]:
    try:
        result: object = json.loads(
            raw, object_pairs_hook=_unique, parse_float=Decimal, parse_constant=_constant
        )
        return _object(result)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise RecoveryError("INVALID_JSON") from exc


def _time(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryError("INVALID_TIME") from exc
    _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "NAIVE_TIME")
    return parsed.astimezone(UTC)


def _decimal(value: object) -> Decimal:
    _require(type(value) in (str, int, Decimal), "EXACT_NUMBER_REQUIRED")
    text = str(value)
    _require(len(text) <= 64, "NUMERIC_SIZE")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise RecoveryError("INVALID_NUMBER") from exc
    _require(result.is_finite(), "NONFINITE_NUMBER")
    return result


@dataclass(frozen=True, slots=True)
class Contract:
    ticker: str
    event: str
    kind: str
    floor: Decimal | None
    cap: Decimal | None
    primary: str
    secondary: str
    settled_hint: datetime


@dataclass(frozen=True, slots=True)
class Family:
    city: str
    target_date: str
    event: str
    contracts: tuple[Contract, ...]


def _ladder(family: Family) -> None:
    rows = family.contracts
    _require(len(rows) == 6 and len({r.ticker for r in rows}) == 6, "SIX_UNIQUE_SIBLINGS")
    _require(all(r.event == family.event for r in rows), "EVENT_MISMATCH")
    _require(all(r.ticker.startswith(family.event + "-") for r in rows), "TICKER_PREFIX")
    _require(sorted(r.kind for r in rows) == ["between"] * 4 + ["greater", "less"], "SHAPE")
    lower = next(r for r in rows if r.kind == "less")
    upper = next(r for r in rows if r.kind == "greater")
    _require(lower.floor is None and upper.cap is None, "TAIL_UNUSED_BOUND")
    base = lower.cap
    _require(base is not None and base == base.to_integral_value(), "INTEGRAL_LADDER")
    base = cast(Decimal, base)
    _require(upper.floor == base + 7, "TAIL_GAP")
    _require(lower.ticker == f"{family.event}-T{base:.0f}", "LOWER_TICKER")
    _require(upper.ticker == f"{family.event}-T{base + 7:.0f}", "UPPER_TICKER")
    middle = sorted((r for r in rows if r.kind == "between"), key=lambda r: r.floor or 0)
    for i, row in enumerate(middle):
        floor = base + 2 * i
        _require(row.floor == floor and row.cap == floor + 1, "LADDER_GAP")
        _require(row.ticker == f"{family.event}-B{floor + Decimal('0.5'):.1f}", "BIN_TICKER")


def _frozen_root() -> dict[str, object]:
    with ROSTER.open("rb") as stream:
        raw = stream.read(1_048_577)
    _require(len(raw) <= 1_048_576, "ROSTER_SIZE")
    _require(hashlib.sha256(raw).hexdigest() == ROSTER_SHA256, "ROSTER_DIGEST")
    return _json(raw)


def frozen_families() -> tuple[Family, ...]:
    """Load only this package's fixed, digest-bound 14-family / 84-ticker roster."""
    root = _frozen_root()
    source = root.get("families")
    _require(type(source) is list, "FAMILIES_REQUIRED")
    families: list[Family] = []
    for item in cast(list[object], source):
        data = _object(item)
        event = _text(data["event_ticker"])
        material = data["contracts"]
        _require(type(material) is list, "CONTRACTS_REQUIRED")
        contracts = []
        for item_contract in cast(list[object], material):
            c = _object(item_contract)
            contracts.append(
                Contract(
                    _text(c["ticker"]),
                    event,
                    _text(c["strike_type"]),
                    None if c["floor_strike"] is None else _decimal(c["floor_strike"]),
                    None if c["cap_strike"] is None else _decimal(c["cap_strike"]),
                    _text(c["rules_primary"]),
                    _text(c["rules_secondary"]),
                    _time(c["settlement_ts_routing_hint"]),
                )
            )
        family = Family(_text(data["city_id"]), _text(data["target_date"]), event, tuple(contracts))
        _ladder(family)
        families.append(family)
    _require(len(families) == 14, "FOURTEEN_FAMILIES")
    _require(len({f.event for f in families}) == 14, "DUPLICATE_FAMILY")
    _require(len({c.ticker for f in families for c in f.contracts}) == 84, "EIGHTY_FOUR_TICKERS")
    return tuple(families)


@dataclass(frozen=True, slots=True)
class PlannedGet:
    path: str
    ticker: str | None
    method: Literal["GET"] = "GET"
    origin: str = ORIGIN
    headers: tuple[tuple[str, str], ...] = HEADERS


def cutoff_request() -> PlannedGet:
    return PlannedGet(BASE + "/historical/cutoff", None)


def plan_after_cutoff(raw: bytes) -> tuple[PlannedGet, ...]:
    """Use source settlement timestamps, never a target-day/Chicago age heuristic.

    Exact equality is conservatively unverified, not guessed or tried on both
    routes. The hints are pinned old archive data; changed response times fail.
    """
    _require(type(raw) is bytes and len(raw) <= MAX_BODY_BYTES, "CUTOFF_SIZE")
    payload = _json(raw)
    cutoff = _time(payload.get("market_settled_ts"))
    _time(payload.get("trades_created_ts"))
    _time(payload.get("orders_updated_ts"))
    requests = []
    for family in frozen_families():
        for contract in family.contracts:
            _require(contract.settled_hint != cutoff, "CUTOFF_EQUALITY_UNVERIFIED")
            route = "/historical/markets/" if contract.settled_hint < cutoff else "/markets/"
            requests.append(PlannedGet(BASE + route + contract.ticker, contract.ticker))
    _require(len(requests) == MAX_REQUESTS - 1, "REQUEST_COUNT")
    return tuple(requests)


@dataclass(frozen=True, slots=True)
class SimulatedResponse:
    """Synthetic bytes and logical duration only; never a real acquisition receipt."""

    request_path: str
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),)
    elapsed_ms: int = 1
    complete: bool = True
    cancelled: bool = False

    def __post_init__(self) -> None:
        _require(type(self.request_path) is str and type(self.body) is bytes, "WIRE_TYPES")
        _require(type(self.status) is int and 100 <= self.status <= 599, "HTTP_STATUS")
        _require(type(self.elapsed_ms) is int and self.elapsed_ms >= 0, "DURATION")
        _require(type(self.complete) is bool and type(self.cancelled) is bool, "FLAGS")
        _require(type(self.headers) is tuple, "HEADERS")
        for pair in self.headers:
            _require(type(pair) is tuple and len(pair) == 2, "HEADER_PAIR")
            _require(type(pair[0]) is str and type(pair[1]) is str, "HEADER_TYPES")


@dataclass(frozen=True, slots=True)
class ReplayReceipt:
    request: PlannedGet
    status: int | None
    state: str
    retained_body: bytes
    retained_sha256: str
    complete_body: bool
    simulated_start_ms: int
    simulated_finish_ms: int
    simulation_only: Literal[True] = True

    def __post_init__(self) -> None:
        _require(self.simulation_only is True, "SIMULATION_ONLY")


@dataclass(frozen=True, slots=True)
class FamilyReplay:
    event: str
    state: str
    reason: str
    source_custody_attested: Literal[False] = False
    point_score_eligible: Literal[False] = False

    def __post_init__(self) -> None:
        _require(
            self.source_custody_attested is False and self.point_score_eligible is False,
            "NO_AUTHORITY",
        )


@dataclass(frozen=True, slots=True)
class PreservedCityDay:
    city: str
    target_date: str
    capture_state: str
    prior_outcome_state: str
    depth_abstentions: int
    historical_reference_conflict: bool
    recovery_state: str
    point_score_authorized: Literal[False] = False

    def __post_init__(self) -> None:
        _require(self.point_score_authorized is False, "NO_AUTHORITY")


@dataclass(frozen=True, slots=True)
class ReplayResult:
    receipts: tuple[ReplayReceipt, ...]
    families: tuple[FamilyReplay, ...]
    request_count: int
    logical_elapsed_ms: int
    preserved_city_days: tuple[PreservedCityDay, ...]
    full_city_days_preserved: Literal[60] = 60
    full_date_clusters_preserved: Literal[12] = 12
    simulation_only: Literal[True] = True
    live_authorized: Literal[False] = False
    point_score_authorized: Literal[False] = False
    paper_trading_authority: Literal["NONE"] = "NONE"

    def __post_init__(self) -> None:
        _require(
            self.simulation_only is True and self.live_authorized is False, "NO_LIVE_AUTHORITY"
        )
        _require(
            self.point_score_authorized is False and self.paper_trading_authority == "NONE",
            "NO_AUTHORITY",
        )
        _require(len(self.preserved_city_days) == 60, "FULL_DENOMINATOR")


def _preserved_rows(
    families: tuple[Family, ...], outputs: tuple[FamilyReplay, ...]
) -> tuple[PreservedCityDay, ...]:
    material = _frozen_root()["full_city_day_roster"]
    _require(type(material) is list, "FULL_ROSTER_REQUIRED")
    recovery = {(f.city, f.target_date): o.state for f, o in zip(families, outputs, strict=True)}
    rows = []
    for item in cast(list[object], material):
        d = _object(item)
        city, day = _text(d["city_id"]), _text(d["target_date"])
        count = d["depth_abstentions"]
        _require(type(count) is int and 0 <= count <= 6, "ABSTENTIONS")
        conflict = d["historical_reference_conflict"]
        _require(type(conflict) is bool, "REFERENCE_CONFLICT")
        rows.append(
            PreservedCityDay(
                city,
                day,
                _text(d["capture_state"]),
                _text(d["outcome_evidence_status"]),
                cast(int, count),
                cast(bool, conflict),
                recovery.get((city, day), "NOT_A_RECOVERY_TARGET"),
            )
        )
    _require(len(rows) == len({(r.city, r.target_date) for r in rows}) == 60, "FULL_DENOMINATOR")
    _require(len({r.target_date for r in rows}) == 12, "DATE_CLUSTERS")
    return tuple(rows)


def _envelope(response: SimulatedResponse, request: PlannedGet) -> str:
    _require(type(response) is SimulatedResponse, "BUILTIN_SIMULATION_REQUIRED")
    _require(type(response.body) is bytes, "BODY_BYTES_REQUIRED")
    _require(type(response.status) is int and 100 <= response.status <= 599, "HTTP_STATUS")
    _require(type(response.elapsed_ms) is int and response.elapsed_ms >= 0, "DURATION")
    _require(type(response.complete) is bool and type(response.cancelled) is bool, "FLAGS")
    _require(response.request_path == request.path, "REQUEST_PATH_MISMATCH")
    if response.cancelled:
        return "CANCELLED"
    if response.status in (403, 429):
        return f"HTTP_{response.status}"
    if response.elapsed_ms >= REQUEST_TIMEOUT_MS:
        return "REQUEST_TIMEOUT"
    if len(response.body) > MAX_BODY_BYTES:
        return "BODY_LIMIT"
    if not response.complete:
        return "INCOMPLETE_RESPONSE"
    if 300 <= response.status < 400:
        return "REDIRECT_REJECTED"
    if response.status != 200:
        return f"HTTP_{response.status}"
    _require(type(response.headers) is tuple, "HEADERS")
    seen: dict[str, str] = {}
    size = 0
    for key, value in response.headers:
        _require(type(key) is str and type(value) is str, "HEADER_TYPES")
        _require("\r" not in key + value and "\n" not in key + value, "HEADER_CONTROL")
        key = key.lower()
        _require(key not in seen, "DUPLICATE_HEADER")
        seen[key] = value
        size += len(key.encode()) + len(value.encode())
    _require(size <= MAX_HEADER_BYTES, "HEADER_LIMIT")
    _require(
        seen.get("content-type", "").split(";")[0].strip().lower() == "application/json",
        "CONTENT_TYPE",
    )
    if "content-length" in seen:
        _require(seen["content-length"].isdecimal(), "CONTENT_LENGTH")
        _require(int(seen["content-length"]) == len(response.body), "CONTENT_LENGTH")
    return "ENVELOPE_OK"


def _market(raw: bytes, contract: Contract, path: str) -> str:
    payload = _json(raw)
    market = _object(payload.get("market"))
    _require(market.get("ticker") == contract.ticker, "WRONG_TICKER")
    _require(market.get("event_ticker") == contract.event, "WRONG_EVENT")
    _require(market.get("market_type") == "binary", "WRONG_MARKET_TYPE")
    _require(market.get("status") == "finalized", "NOT_FINALIZED")
    _require(market.get("rules_primary") == contract.primary, "PRIMARY_RULE_CHANGED")
    _require(market.get("rules_secondary") == contract.secondary, "SECONDARY_RULE_CHANGED")
    _require(market.get("strike_type") == contract.kind, "STRIKE_KIND_CHANGED")
    for field, expected in (("floor_strike", contract.floor), ("cap_strike", contract.cap)):
        value = market.get(field)
        if expected is None:
            _require(value is None, "UNUSED_BOUND_CHANGED")
        else:
            _require(_decimal(value) == expected, "STRIKE_BOUND_CHANGED")
    exact = _text(market.get("expiration_value"))
    value = _decimal(exact)
    _require(Decimal(-150) < value < Decimal(150), "UNDERLYING_RANGE")
    settled = _time(market.get("settlement_ts"))
    _require(settled == contract.settled_hint, "SETTLEMENT_TIME_CHANGED")
    _require(_time(market.get("close_time")) <= settled, "SETTLEMENT_CHRONOLOGY")
    _require(path.endswith("/" + contract.ticker), "RESPONSE_PATH")
    result = market.get("result")
    _require(result in ("yes", "no"), "RESULT")
    _require(
        _decimal(market.get("settlement_value_dollars")) == (1 if result == "yes" else 0), "PAYOUT"
    )
    if contract.kind == "between":
        _require(contract.floor is not None and contract.cap is not None, "RANGE_BOUNDS")
        predicate = cast(Decimal, contract.floor) <= value <= cast(Decimal, contract.cap)
    elif contract.kind == "less":
        _require(contract.cap is not None, "CAP_REQUIRED")
        predicate = value < cast(Decimal, contract.cap)
    else:
        _require(contract.floor is not None, "FLOOR_REQUIRED")
        predicate = value > cast(Decimal, contract.floor)
    _require(predicate == (result == "yes"), "PAYOUT_PREDICATE_CONFLICT")
    return exact


def replay_offline(
    responses: tuple[SimulatedResponse, ...], *, cancel_before_request: int | None = None
) -> ReplayResult:
    """Deterministic one-at-a-time simulation, with no positive evidence issuance.

    A future separately reviewed live adapter must independently enforce absolute
    deadlines across DNS/TLS/body reads and create-once raw evidence persistence.
    This replay proves only the bounded request contract, not live clock behavior.
    """
    _require(type(responses) is tuple and len(responses) <= MAX_REQUESTS, "RESPONSE_BUDGET")
    _require(cancel_before_request is None or type(cancel_before_request) is int, "CANCEL_INDEX")
    _require(
        cancel_before_request is None or 0 <= cancel_before_request <= MAX_REQUESTS, "CANCEL_INDEX"
    )
    families = frozen_families()
    contracts = tuple(c for f in families for c in f.contracts)
    requests = [cutoff_request()]
    receipts: list[ReplayReceipt] = []
    valid: dict[str, str] = {}
    issues: dict[str, str] = {}
    now = 0
    next_start = 0
    stopped: str | None = None
    attempted = 0
    for index in range(MAX_REQUESTS):
        if index >= len(requests):
            break
        request = requests[index]
        start = max(now, next_start)
        if stopped is None:
            if cancel_before_request is not None and index >= cancel_before_request:
                stopped = "CANCELLED"
            elif start >= TOTAL_DEADLINE_MS:
                stopped = "TOTAL_DEADLINE"
        state = stopped
        raw = b""
        complete = False
        status: int | None = None
        attempted_this = False
        finish = min(start, TOTAL_DEADLINE_MS)
        if state is None:
            if index >= len(responses):
                state = "NO_SYNTHETIC_RESPONSE"
                stopped = state
            else:
                response = responses[index]
                _require(type(response) is SimulatedResponse, "BUILTIN_SIMULATION_REQUIRED")
                _require(type(response.body) is bytes, "BODY_BYTES_REQUIRED")
                _require(type(response.elapsed_ms) is int and response.elapsed_ms >= 0, "DURATION")
                attempted += 1
                attempted_this = True
                status = response.status
                raw = response.body[:MAX_BODY_BYTES]
                complete = response.complete and len(response.body) <= MAX_BODY_BYTES
                finish = min(
                    start + response.elapsed_ms, start + REQUEST_TIMEOUT_MS, TOTAL_DEADLINE_MS
                )
                try:
                    state = _envelope(response, request)
                    if finish >= TOTAL_DEADLINE_MS:
                        state = "TOTAL_DEADLINE"
                    if state == "ENVELOPE_OK":
                        if index == 0:
                            requests.extend(plan_after_cutoff(raw))
                            state = "SIMULATED_CUTOFF_ONLY"
                        else:
                            contract = contracts[index - 1]
                            valid[contract.ticker] = _market(raw, contract, request.path)
                            state = "SIMULATED_MARKET_ONLY"
                except (RecoveryError, ArithmeticError) as exc:
                    state = str(exc) or "INVALID_RESPONSE"
                if response.status in (403, 429):
                    stopped = f"HTTP_{response.status}"
                elif state in ("CANCELLED", "TOTAL_DEADLINE"):
                    stopped = state
                if index == 0 and state != "SIMULATED_CUTOFF_ONLY":
                    stopped = state
        if not attempted_this:
            start = finish = now
        if request.ticker is not None and state != "SIMULATED_MARKET_ONLY":
            issues[request.ticker] = state
        receipts.append(
            ReplayReceipt(
                request,
                status,
                state,
                raw,
                hashlib.sha256(raw).hexdigest(),
                complete,
                min(start, TOTAL_DEADLINE_MS),
                finish,
            )
        )
        if attempted_this:
            now = finish
            next_start = start + MIN_START_INTERVAL_MS
    outputs = []
    for family in families:
        values = [valid[c.ticker] for c in family.contracts if c.ticker in valid]
        reason = next((issues[c.ticker] for c in family.contracts if c.ticker in issues), None)
        if reason is None and len(values) != 6:
            reason = stopped or "INCOMPLETE_FAMILY"
        if reason is None and len(set(values)) != 1:
            reason = "SIBLING_TEMPERATURE_OR_PRECISION_CONFLICT"
        outputs.append(
            FamilyReplay(
                family.event,
                "SIMULATED_METADATA_CONSISTENT" if reason is None else "UNVERIFIED",
                reason or "SIMULATION_IS_NOT_SOURCE_AUTHORITY",
            )
        )
    family_outputs = tuple(outputs)
    return ReplayResult(
        tuple(receipts), family_outputs, attempted, now, _preserved_rows(families, family_outputs)
    )
