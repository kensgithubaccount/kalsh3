"""First-party prospective KXGDP fee authority.

Only :func:`acquire_fee_authority` is a canonical authority entrypoint.  The
parser and resolver seams are deliberately private so deterministic tests can
exercise them without making caller-authored evidence authoritative.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from services.opportunity_engine.fees import FeeCalculation as _FeeCalculation
from services.opportunity_engine.fees import FeePolicy as _FeePolicy
from services.opportunity_engine.fees import FeeType as _FeeType
from services.opportunity_engine.fees import calculate_fee as _calculate_fee

PDF_URL: Final = "https://kalshi.com/docs/kalshi-fee-schedule.pdf"
FEE_CHANGES_URL: Final = (
    "https://external-api.kalshi.com/trade-api/v2/series/fee_changes"
    "?series_ticker=KXGDP&show_historical=true"
)
KXGDP: Final = "KXGDP"
MAX_RESPONSE_BYTES: Final = 8_000_000
TIMEOUT_SECONDS: Final = 10.0
RESOLVER_ID: Final = "d1-g3-kxgdp-fee-resolver-v2"
FORMULA_VERSION: Final = "price-times-complement-times-quantity-v1"
AUDITED_TAKER_COEFFICIENT: Final = Decimal("0.07")
AUDITED_MAKER_COEFFICIENT: Final = Decimal("0.0175")


class FeeAuthorityStatus(StrEnum):
    COMPLETE = "COMPLETE FEE AUTHORITY"
    INCOMPLETE = "EVIDENCE_INCOMPLETE"
    BLOCKED = "BLOCKED"


class FeeAuthorityError(ValueError):
    """Evidence could not prove the exact prospective fee policy."""


_RAW_EVIDENCE_CAPABILITY: Final = object()
_PARSER_CAPABILITY: Final = object()
_RESOLVER_CAPABILITY: Final = object()
_ISSUED: dict[int, str] = {}
_ISSUED_POLICIES: dict[int, str] = {}


def _register(value: object) -> None:
    _ISSUED[id(value)] = hashlib.sha256(repr(value).encode()).hexdigest()


def _require_issued(value: object, label: str) -> None:
    expected = hashlib.sha256(repr(value).encode()).hexdigest()
    if _ISSUED.get(id(value)) != expected:
        raise FeeAuthorityError(f"{label} is reconstructed or not issuer-issued")


@dataclass(frozen=True, slots=True, init=False)
class _RawEvidence:
    source_url: str
    method: str
    status: int
    content_type: str
    acquired_at: datetime
    body: bytes
    body_sha256: str

    def __init__(
        self,
        source_url: str,
        method: str,
        status: int,
        content_type: str,
        acquired_at: datetime,
        body: bytes,
        body_sha256: str,
        *,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _RAW_EVIDENCE_CAPABILITY:
            raise FeeAuthorityError("raw evidence requires reviewed acquisition capability")
        for name, value in locals().copy().items():
            if name not in {"self", "_capability"}:
                object.__setattr__(self, name, value)
        _register(self)


@dataclass(frozen=True, slots=True, init=False)
class _FeeChange:
    change_id: str
    effective_at: datetime
    fee_type: _FeeType
    multiplier: Decimal
    raw: dict[str, object]

    def __init__(self, *, capability: object, **values: object) -> None:
        if capability is not _PARSER_CAPABILITY:
            raise FeeAuthorityError("parsed fee changes require issuer parser capability")
        for name in ("change_id", "effective_at", "fee_type", "multiplier", "raw"):
            object.__setattr__(self, name, values[name])
        _register(self)


@dataclass(frozen=True, slots=True, init=False)
class _FeeSchedule:
    document_identity: str
    effective_date: date
    taker_coefficient: Decimal
    maker_coefficient: Decimal
    taker_multiplier: Decimal
    maker_multiplier: Decimal
    raw: _RawEvidence
    currentness_proven: bool

    def __init__(self, *, capability: object, **values: object) -> None:
        if capability is not _PARSER_CAPABILITY:
            raise FeeAuthorityError("parsed fee schedule requires issuer parser capability")
        for name in (
            "document_identity",
            "effective_date",
            "taker_coefficient",
            "maker_coefficient",
            "taker_multiplier",
            "maker_multiplier",
            "raw",
            "currentness_proven",
        ):
            object.__setattr__(self, name, values[name])
        _register(self)


@dataclass(frozen=True, slots=True, init=False)
class _FeeChangeBatch:
    records: tuple[_FeeChange, ...]
    raw: _RawEvidence
    exhaustive_proven: bool

    def __init__(self, *, capability: object, **values: object) -> None:
        if capability is not _PARSER_CAPABILITY:
            raise FeeAuthorityError("parsed fee changes require issuer parser capability")
        for name in ("records", "raw", "exhaustive_proven"):
            object.__setattr__(self, name, values[name])
        _register(self)


@dataclass(frozen=True, slots=True, init=False)
class _AuthorityCandidate:
    status: FeeAuthorityStatus
    reason: str | None
    policy: _FeePolicy | None
    applicable_change_ids: tuple[str, ...]
    resolver_id: str

    def __init__(self, *, capability: object, **values: object) -> None:
        if capability is not _RESOLVER_CAPABILITY:
            raise FeeAuthorityError("authority candidates require reviewed resolver capability")
        for name in ("status", "reason", "policy", "applicable_change_ids", "resolver_id"):
            object.__setattr__(self, name, values[name])
        _register(self)


class FeeAuthority:
    """Immutable result issued only by the fixed-source acquisition path."""

    _applicable_change_ids: tuple[str, ...]
    _fee_change_evidence: _RawEvidence | None
    _pdf_evidence: _RawEvidence | None
    _policy: _FeePolicy | None
    _reason: str | None
    _resolver_id: str
    _sealed: bool
    _status: FeeAuthorityStatus

    __slots__ = (
        "_applicable_change_ids",
        "_fee_change_evidence",
        "_pdf_evidence",
        "_policy",
        "_reason",
        "_resolver_id",
        "_sealed",
        "_status",
    )

    def __new__(cls, *args: object, **kwargs: object) -> FeeAuthority:
        candidate = kwargs.pop("_candidate", None)
        if args or type(candidate) is not _AuthorityCandidate:
            raise TypeError("FeeAuthority is issued by acquire_fee_authority only")
        _require_issued(candidate, "authority candidate")
        return super().__new__(cls)

    def __init__(
        self,
        *,
        _candidate: _AuthorityCandidate,
        _pdf: _RawEvidence | None,
        _changes: _RawEvidence | None,
    ) -> None:
        _require_issued(_candidate, "authority candidate")
        object.__setattr__(self, "_status", _candidate.status)
        object.__setattr__(self, "_reason", _candidate.reason)
        object.__setattr__(self, "_policy", _candidate.policy)
        object.__setattr__(self, "_pdf_evidence", _pdf)
        object.__setattr__(self, "_fee_change_evidence", _changes)
        object.__setattr__(self, "_applicable_change_ids", _candidate.applicable_change_ids)
        object.__setattr__(self, "_resolver_id", _candidate.resolver_id)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("FeeAuthority is immutable")
        object.__setattr__(self, name, value)

    @property
    def status(self) -> FeeAuthorityStatus:
        return self._status

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def policy(self) -> _FeePolicy | None:
        return self._policy

    @property
    def pdf_evidence(self) -> _RawEvidence | None:
        return self._pdf_evidence

    @property
    def fee_change_evidence(self) -> _RawEvidence | None:
        return self._fee_change_evidence

    @property
    def applicable_change_ids(self) -> tuple[str, ...]:
        return self._applicable_change_ids

    @property
    def resolver_id(self) -> str:
        return self._resolver_id

    def calculate_one_contract(self, price: Decimal) -> _FeeCalculation:
        if self.status is not FeeAuthorityStatus.COMPLETE or self.policy is None:
            raise FeeAuthorityError("fee authority is incomplete")
        # The reviewed experiment covers one whole contract at a cent-aligned
        # marketable/taker price only.  It does not authorize arbitrary fills,
        # subpenny prices, maker economics, rebates, or member-class variants.
        if price != price.quantize(Decimal("0.01")):
            raise FeeAuthorityError("unsupported subpenny price")
        return _calculate_fee(self.policy, price, Decimal("1"), maker=False)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: object, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> urllib.request.Request | None:
        del req, fp, code, msg, headers, newurl
        raise FeeAuthorityError("redirect rejected")


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise FeeAuthorityError("decision instant must be timezone-aware")
    return value.astimezone(UTC)


def _fetch(url: str, *, accept: str) -> _RawEvidence:
    if url not in {PDF_URL, FEE_CHANGES_URL}:
        raise FeeAuthorityError("source URL is outside fixed authority")
    request = urllib.request.Request(  # noqa: S310 - URL is one of two fixed HTTPS constants
        url, method="GET", headers={"Accept": accept}
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)
        status = exc.code
        content_type = exc.headers.get("Content-Type", "").split(";", 1)[0].lower()
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise FeeAuthorityError(f"fixed source acquisition failed: {exc}") from exc
    acquired_at = datetime.now(UTC)
    if len(body) > MAX_RESPONSE_BYTES:
        raise FeeAuthorityError("response truncated or exceeds bound")
    return _RawEvidence(
        url,
        "GET",
        status,
        content_type,
        acquired_at,
        body,
        hashlib.sha256(body).hexdigest(),
        _capability=_RAW_EVIDENCE_CAPABILITY,
    )


def _validate_evidence(evidence: _RawEvidence) -> None:
    if type(evidence) is not _RawEvidence:
        raise FeeAuthorityError("evidence has an unsupported runtime type")
    _require_issued(evidence, "raw evidence")
    if evidence.source_url not in {PDF_URL, FEE_CHANGES_URL}:
        raise FeeAuthorityError("evidence source is outside fixed authority")
    if evidence.method != "GET":
        raise FeeAuthorityError("evidence method is outside fixed authority")
    if type(evidence.body) is not bytes or not evidence.body:
        raise FeeAuthorityError("evidence body is missing")
    if type(evidence.acquired_at) is not datetime or evidence.acquired_at.tzinfo is None:
        raise FeeAuthorityError("evidence acquisition timestamp is invalid")
    if evidence.body_sha256 != hashlib.sha256(evidence.body).hexdigest():
        raise FeeAuthorityError("evidence body hash mismatch")


def _pdf_text(body: bytes) -> str:
    if not body.startswith(b"%PDF-") or b"%%EOF" not in body[-128:]:
        raise FeeAuthorityError("PDF is truncated or malformed")
    chunks = [body]
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", body, re.S):
        try:
            chunks.append(zlib.decompress(match.group(1)))
        except zlib.error:
            continue
    strings = re.findall(rb"\((?:\\.|[^()])*\)", b"\n".join(chunks))
    text = b" ".join(strings).decode("latin1", errors="strict")
    text += "\n" + b"\n".join(chunks).decode("latin1", errors="ignore")
    return re.sub(r"\s+", " ", text)


def _one_match(pattern: str, text: str, label: str, *, flags: int = re.I) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, flags))
    if len(matches) != 1:
        raise FeeAuthorityError(f"{label} is missing, duplicated, or ambiguous")
    return matches[0]


def _parse_fee_schedule(evidence: _RawEvidence) -> _FeeSchedule:
    _validate_evidence(evidence)
    if evidence.source_url != PDF_URL or evidence.status != 200:
        raise FeeAuthorityError("official fee PDF unavailable")
    if evidence.content_type != "application/pdf":
        raise FeeAuthorityError("official fee source has wrong content type")
    text = _pdf_text(evidence.body)
    title = _one_match(r"\b(Kalshi Fee Schedule)\b", text, "fee document identity").group(1)
    date_match = _one_match(
        r"Last updated and effective\s*:?\s*([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
        text,
        "fee document effective date",
    )
    try:
        effective_date = datetime.strptime(date_match.group(1), "%B %d, %Y").date()
    except ValueError as exc:
        raise FeeAuthorityError("fee document effective date is malformed") from exc
    # The canonical fixed PDF must itself declare the schedule as current; its
    # effective date alone is never treated as a non-supersession guarantee.
    _one_match(r"\bcurrent\s+general\s+fee\b", text, "current-fee declaration")

    formula_pattern = (
        r"fees\s*=\s*round\s*up\s*\(\s*(?:M\s*[\u00d7*x]\s*)?"
        r"(?P<coefficient>0\.\d+)\s*[\u00d7*x]\s*C\s*[\u00d7*x]\s*P\s*"
        r"[\u00d7*x]\s*\(\s*1\s*-\s*P\s*\)\s*\)"
    )
    maker_section = _one_match(r"\bMaker\s+Fees\b", text, "maker-fee section")
    taker_matches = list(re.finditer(formula_pattern, text[: maker_section.start()], re.I))
    maker_matches = list(re.finditer(formula_pattern, text[maker_section.end() :], re.I))
    if len(taker_matches) != 1 or len(maker_matches) != 1:
        raise FeeAuthorityError("maker/taker formula set is missing, duplicated, or ambiguous")
    taker_coefficient = Decimal(taker_matches[0].group("coefficient"))
    maker_coefficient = Decimal(maker_matches[0].group("coefficient"))
    if taker_coefficient == maker_coefficient:
        raise FeeAuthorityError("maker and taker coefficients are ambiguous")
    if (
        taker_coefficient != AUDITED_TAKER_COEFFICIENT
        or maker_coefficient != AUDITED_MAKER_COEFFICIENT
    ):
        raise FeeAuthorityError("fee formula semantics differ from the reviewed schedule")

    _one_match(r"\bP\s+(?:is|=)\s+contract price\b", text, "price definition")
    _one_match(r"\bC\s+(?:is|=)\s+contract quantity\b", text, "quantity definition")
    header = _one_match(r"\bMaker\s+multiplier\s+Taker\s+multiplier\b", text, "fee table header")
    if re.search(r"\bTaker\s+multiplier\s+Maker\s+multiplier\b", text, re.I):
        raise FeeAuthorityError("maker/taker table columns are swapped or ambiguous")
    rows = list(
        re.finditer(
            r"\bKXGDP\s+(?P<maker>\d+(?:\.\d+)?)\s+(?P<taker>\d+(?:\.\d+)?)\b",
            text[header.end() :],
            re.I,
        )
    )
    if len(rows) != 1:
        raise FeeAuthorityError("KXGDP row missing, duplicated, or ambiguous")
    row = rows[0]
    maker_multiplier = Decimal(row.group("maker"))
    taker_multiplier = Decimal(row.group("taker"))
    if maker_multiplier < 0 or taker_multiplier < 0:
        raise FeeAuthorityError("KXGDP multiplier is invalid")
    return _FeeSchedule(
        capability=_PARSER_CAPABILITY,
        document_identity=f"{title}|effective-{effective_date.isoformat()}",
        effective_date=effective_date,
        taker_coefficient=taker_coefficient,
        maker_coefficient=maker_coefficient,
        taker_multiplier=taker_multiplier,
        maker_multiplier=maker_multiplier,
        raw=evidence,
        currentness_proven=True,
    )


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise FeeAuthorityError(f"{field} is not an exact numeric value")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise FeeAuthorityError(f"{field} is malformed") from exc
    if not result.is_finite() or result < 0:
        raise FeeAuthorityError(f"{field} is invalid")
    return result


def _parse_fee_changes(evidence: _RawEvidence) -> _FeeChangeBatch:
    _validate_evidence(evidence)
    if evidence.source_url != FEE_CHANGES_URL or evidence.status != 200:
        raise FeeAuthorityError("fee-change source unavailable")
    if evidence.content_type != "application/json":
        raise FeeAuthorityError("fee-change source has wrong content type")
    try:
        payload = json.loads(evidence.body, parse_int=Decimal, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeeAuthorityError("fee-change JSON malformed") from exc
    if not isinstance(payload, dict) or set(payload) != {"series_fee_change_arr"}:
        raise FeeAuthorityError("fee-change JSON schema is ambiguous or unsupported")
    records = payload["series_fee_change_arr"]
    if not isinstance(records, list):
        raise FeeAuthorityError("fee-change array missing")
    result: list[_FeeChange] = []
    seen_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise FeeAuthorityError("fee-change record is malformed")
        required = {"id", "series_ticker", "fee_type", "fee_multiplier", "scheduled_ts"}
        if set(record) != required:
            raise FeeAuthorityError("fee-change record schema is incomplete or ambiguous")
        if record["series_ticker"] != KXGDP or not isinstance(record["id"], str):
            raise FeeAuthorityError("fee-change record is not positively bound to KXGDP")
        change_id = record["id"]
        if not change_id or change_id in seen_ids:
            raise FeeAuthorityError("fee-change ID is missing or duplicated")
        seen_ids.add(change_id)
        raw_effective = record["scheduled_ts"]
        if not isinstance(raw_effective, str):
            raise FeeAuthorityError("fee-change effective timestamp missing")
        try:
            effective = _utc(datetime.fromisoformat(raw_effective.replace("Z", "+00:00")))
            fee_type = _FeeType(str(record["fee_type"]))
        except (KeyError, ValueError) as exc:
            raise FeeAuthorityError("fee-change type or timestamp malformed") from exc
        if fee_type not in {_FeeType.QUADRATIC, _FeeType.QUADRATIC_WITH_MAKER_FEES}:
            raise FeeAuthorityError("fee-change fee type unsupported")
        result.append(
            _FeeChange(
                capability=_PARSER_CAPABILITY,
                change_id=change_id,
                effective_at=effective,
                fee_type=fee_type,
                multiplier=_decimal(record["fee_multiplier"], "fee-change multiplier"),
                raw=dict(record),
            )
        )
    # The response schema does not prove market- and event-level override
    # absence, nor complete coverage for the decision interval.  An empty
    # array is therefore data, not an exhaustiveness assertion.
    return _FeeChangeBatch(
        capability=_PARSER_CAPABILITY, records=tuple(result), raw=evidence, exhaustive_proven=False
    )


def _candidate_incomplete(reason: str) -> _AuthorityCandidate:
    return _AuthorityCandidate(
        capability=_RESOLVER_CAPABILITY,
        status=FeeAuthorityStatus.INCOMPLETE,
        reason=reason,
        policy=None,
        applicable_change_ids=(),
        resolver_id=RESOLVER_ID,
    )


def _resolve_fee_authority(
    decision_at: datetime, schedule: _FeeSchedule, changes: _FeeChangeBatch
) -> _AuthorityCandidate:
    _require_issued(schedule, "fee schedule")
    _require_issued(changes, "fee-change batch")
    instant = _utc(decision_at)
    if not schedule.currentness_proven:
        return _candidate_incomplete("current fee-schedule status is unproven")
    if not changes.exhaustive_proven:
        return _candidate_incomplete("fee-change/override completeness is unproven")
    if instant.date() <= schedule.effective_date:
        return _candidate_incomplete(
            "date-only fee effective information cannot prove intraday applicability"
        )
    applicable = tuple(change for change in changes.records if change.effective_at <= instant)
    by_effective: dict[datetime, _FeeChange] = {}
    for change in applicable:
        if change.effective_at in by_effective:
            return _candidate_incomplete("conflicting or ambiguous same-time fee changes")
        by_effective[change.effective_at] = change
    if applicable:
        chosen = max(applicable, key=lambda change: change.effective_at)
        fee_type, multiplier = chosen.fee_type, chosen.multiplier
        ids = tuple(change.change_id for change in applicable)
        effective = chosen.effective_at
    else:
        fee_type, multiplier, effective, ids = (
            _FeeType.QUADRATIC,
            schedule.taker_multiplier,
            datetime.combine(schedule.effective_date, datetime.min.time(), tzinfo=UTC),
            (),
        )
    policy = _FeePolicy(
        "kxgdp-taker-" + effective.date().isoformat(),
        fee_type,
        multiplier,
        effective,
        None,
        FORMULA_VERSION,
        schedule.document_identity,
        True,
        quadratic_coefficient=schedule.taker_coefficient,
        maker_quadratic_coefficient=schedule.maker_coefficient,
    )
    _ISSUED_POLICIES[id(policy)] = hashlib.sha256(repr(policy).encode()).hexdigest()
    return _AuthorityCandidate(
        capability=_RESOLVER_CAPABILITY,
        status=FeeAuthorityStatus.COMPLETE,
        reason=None,
        policy=policy,
        applicable_change_ids=ids,
        resolver_id=RESOLVER_ID,
    )


def _issue(
    candidate: _AuthorityCandidate,
    pdf: _RawEvidence | None,
    changes: _RawEvidence | None,
) -> FeeAuthority:
    _require_issued(candidate, "authority candidate")
    if pdf is not None:
        _validate_evidence(pdf)
    if changes is not None:
        _validate_evidence(changes)
    if candidate.status is FeeAuthorityStatus.COMPLETE and (
        candidate.policy is None
        or _ISSUED_POLICIES.get(id(candidate.policy))
        != hashlib.sha256(repr(candidate.policy).encode()).hexdigest()
    ):
        raise FeeAuthorityError("complete authority requires issuer-created policy")
    return FeeAuthority(
        _candidate=candidate,
        _pdf=pdf,
        _changes=changes,
    )


def _incomplete(
    reason: str,
    pdf: _RawEvidence | None = None,
    changes: _RawEvidence | None = None,
) -> FeeAuthority:
    return _issue(
        _AuthorityCandidate(
            capability=_RESOLVER_CAPABILITY,
            status=FeeAuthorityStatus.INCOMPLETE,
            reason=reason,
            policy=None,
            applicable_change_ids=(),
            resolver_id=RESOLVER_ID,
        ),
        pdf,
        changes,
    )


def acquire_fee_authority(decision_at: datetime) -> FeeAuthority:
    """Acquire fixed first-party evidence and resolve the prospective KXGDP policy."""
    try:
        pdf = _fetch(PDF_URL, accept="application/pdf")
        changes = _fetch(FEE_CHANGES_URL, accept="application/json")
        instant = _utc(decision_at)
        if instant > pdf.acquired_at or instant > changes.acquired_at:
            return _incomplete(
                "decision instant is beyond contemporaneous acquisition evidence", pdf, changes
            )
        schedule = _parse_fee_schedule(pdf)
        parsed_changes = _parse_fee_changes(changes)
        return _issue(_resolve_fee_authority(decision_at, schedule, parsed_changes), pdf, changes)
    except FeeAuthorityError as exc:
        return _incomplete(str(exc))


__all__ = ["FeeAuthority", "FeeAuthorityStatus", "acquire_fee_authority"]
