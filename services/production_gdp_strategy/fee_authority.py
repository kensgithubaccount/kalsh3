"""First-party prospective KXGDP fee authority.

This module owns acquisition and issues a fee policy only after the official fee
PDF and the exact public fee-change query have been retained and parsed.  It is
deliberately not connected to the decision runner yet.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from services.opportunity_engine.fees import FeeCalculation, FeePolicy, FeeType, calculate_fee

PDF_URL: Final = "https://kalshi.com/docs/kalshi-fee-schedule.pdf"
FEE_CHANGES_URL: Final = (
    "https://external-api.kalshi.com/trade-api/v2/series/fee_changes"
    "?series_ticker=KXGDP&show_historical=true"
)
KXGDP: Final = "KXGDP"
MAX_RESPONSE_BYTES: Final = 8_000_000
TIMEOUT_SECONDS: Final = 10.0
RESOLVER_ID: Final = "d1-g3-kxgdp-fee-resolver-v1"
FORMULA_VERSION: Final = "price-times-complement-times-quantity-v1"


class FeeAuthorityStatus(StrEnum):
    COMPLETE = "COMPLETE FEE AUTHORITY"
    INCOMPLETE = "EVIDENCE_INCOMPLETE"
    BLOCKED = "BLOCKED"


class FeeAuthorityError(ValueError):
    """Evidence could not prove the exact prospective fee policy."""


@dataclass(frozen=True, slots=True)
class RawEvidence:
    source_url: str
    status: int
    content_type: str
    acquired_at: datetime
    body: bytes
    body_sha256: str


@dataclass(frozen=True, slots=True)
class FeeChange:
    change_id: str | None
    effective_at: datetime
    fee_type: FeeType
    multiplier: Decimal
    raw: dict[str, object]


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    document_identity: str
    effective_at: datetime
    taker_coefficient: Decimal
    maker_coefficient: Decimal
    taker_multiplier: Decimal
    maker_multiplier: Decimal
    raw: RawEvidence


@dataclass(frozen=True, slots=True)
class FeeAuthority:
    status: FeeAuthorityStatus
    reason: str | None
    policy: FeePolicy | None
    pdf_evidence: RawEvidence | None
    fee_change_evidence: RawEvidence | None
    applicable_change_ids: tuple[str, ...]
    resolver_id: str

    def calculate_one_contract(self, price: Decimal) -> FeeCalculation:
        if self.status is not FeeAuthorityStatus.COMPLETE or self.policy is None:
            raise FeeAuthorityError("fee authority is incomplete")
        return calculate_fee(self.policy, price, Decimal("1"), maker=False)


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


def _fetch(url: str, *, accept: str) -> RawEvidence:
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
    return RawEvidence(
        url, status, content_type, acquired_at, body, hashlib.sha256(body).hexdigest()
    )


def _validate_evidence(evidence: RawEvidence) -> None:
    if evidence.source_url not in {PDF_URL, FEE_CHANGES_URL}:
        raise FeeAuthorityError("evidence source is outside fixed authority")
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
        stream = match.group(1)
        try:
            chunks.append(zlib.decompress(stream))
        except zlib.error:
            continue
    strings = re.findall(rb"\((?:\\.|[^()])*\)", b"\n".join(chunks))
    text = b" ".join(strings).decode("latin1", errors="strict")
    text += "\n" + b"\n".join(chunks).decode("latin1", errors="ignore")
    return re.sub(r"\s+", " ", text)


def parse_fee_schedule(evidence: RawEvidence) -> FeeSchedule:
    """Parse only the audited July-2026 document layout; drift fails closed."""
    _validate_evidence(evidence)
    if evidence.source_url != PDF_URL or evidence.status != 200:
        raise FeeAuthorityError("official fee PDF unavailable")
    if evidence.content_type != "application/pdf":
        raise FeeAuthorityError("official fee source has wrong content type")
    text = _pdf_text(evidence.body)
    if "Kalshi Fee Schedule" not in text:
        raise FeeAuthorityError("fee document identity missing")
    date_match = re.search(r"Last updated and effective\s*:?\s*July 7, 2026", text, re.I)
    if date_match is None:
        raise FeeAuthorityError("fee document effective date missing or unexpected")
    if not re.search(
        r"round\s*up\s*\(\s*M\s*[\u00d7*]\s*0\.07\s*[\u00d7*]\s*C\s*[\u00d7*]\s*P\s*[\u00d7*]\s*\(\s*1\s*-\s*P\s*\)\s*\)",
        text,
        re.I,
    ):
        raise FeeAuthorityError("taker formula authority missing")
    if not re.search(
        r"round\s*up\s*\(\s*M\s*[\u00d7*]\s*0\.0175\s*[\u00d7*]\s*C\s*[\u00d7*]\s*P\s*[\u00d7*]\s*\(\s*1\s*-\s*P\s*\)\s*\)",
        text,
        re.I,
    ):
        raise FeeAuthorityError("maker formula authority missing")
    if not re.search(r"P\s*(?:is|=).*contract price.*C\s*(?:is|=).*contract quantity", text, re.I):
        raise FeeAuthorityError("price or quantity definition missing")
    if not re.search(r"Maker\s+multiplier\s+Taker\s+multiplier", text, re.I):
        raise FeeAuthorityError("fee table header missing")
    rows = re.findall(r"(?:^|\s)KXGDP\s+([^;|]{0,100})", text, re.I)
    if len(rows) != 1:
        raise FeeAuthorityError("KXGDP row missing, duplicated, or ambiguous")
    numbers = re.findall(r"(?<![A-Za-z])(?:0|1)(?:\.0+)?(?![A-Za-z])", rows[0])
    if len(numbers) != 2:
        raise FeeAuthorityError("KXGDP maker/taker multiplier row is ambiguous")
    return FeeSchedule(
        "kalshi-fee-schedule-effective-2026-07-07",
        datetime(2026, 7, 7, tzinfo=UTC),
        Decimal("0.07"),
        Decimal("0.0175"),
        Decimal(numbers[1]),
        Decimal(numbers[0]),
        evidence,
    )


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise FeeAuthorityError(f"{field} is not an exact Decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise FeeAuthorityError(f"{field} is malformed") from exc
    if not result.is_finite() or result < 0:
        raise FeeAuthorityError(f"{field} is invalid")
    return result


def parse_fee_changes(evidence: RawEvidence) -> tuple[FeeChange, ...]:
    _validate_evidence(evidence)
    if evidence.source_url != FEE_CHANGES_URL or evidence.status != 200:
        raise FeeAuthorityError("fee-change source unavailable")
    if evidence.content_type != "application/json":
        raise FeeAuthorityError("fee-change source has wrong content type")
    try:
        payload = json.loads(evidence.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeeAuthorityError("fee-change JSON malformed") from exc
    if not isinstance(payload, dict):
        raise FeeAuthorityError("fee-change JSON must be an object")
    records = payload.get("fee_changes", payload.get("series_fee_changes"))
    if not isinstance(records, list):
        raise FeeAuthorityError("fee-change array missing")
    result: list[FeeChange] = []
    for record in records:
        if not isinstance(record, dict) or record.get("series_ticker") != KXGDP:
            raise FeeAuthorityError("fee-change record is not positively bound to KXGDP")
        raw_effective = record.get("effective_at", record.get("effective_ts"))
        if not isinstance(raw_effective, str):
            raise FeeAuthorityError("fee-change effective timestamp missing")
        try:
            effective = _utc(datetime.fromisoformat(raw_effective.replace("Z", "+00:00")))
            fee_type = FeeType(str(record["fee_type"]))
        except (KeyError, ValueError) as exc:
            raise FeeAuthorityError("fee-change type or timestamp malformed") from exc
        if fee_type not in {FeeType.QUADRATIC, FeeType.QUADRATIC_WITH_MAKER_FEES}:
            raise FeeAuthorityError("fee-change fee type unsupported")
        result.append(
            FeeChange(
                record.get("change_id", record.get("id")),
                effective,
                fee_type,
                _decimal(record["multiplier"], "fee-change multiplier"),
                dict(record),
            )
        )
    return tuple(result)


def resolve_fee_authority(
    decision_at: datetime, schedule: FeeSchedule, changes: tuple[FeeChange, ...]
) -> FeeAuthority:
    instant = _utc(decision_at)
    if schedule.effective_at > instant:
        return _incomplete("base fee document is future-effective", schedule.raw, None)
    applicable = tuple(change for change in changes if change.effective_at <= instant)
    if applicable:
        fingerprints = {(c.fee_type, c.multiplier) for c in applicable}
        if len(fingerprints) != 1:
            return _incomplete("conflicting applicable KXGDP fee changes", schedule.raw, None)
        chosen = max(applicable, key=lambda c: c.effective_at)
        fee_type, multiplier = chosen.fee_type, chosen.multiplier
        ids = tuple(str(c.change_id) for c in applicable if c.change_id is not None)
        effective = chosen.effective_at
    else:
        fee_type, multiplier, effective, ids = (
            FeeType.QUADRATIC,
            schedule.taker_multiplier,
            schedule.effective_at,
            (),
        )
    policy = FeePolicy(
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
    return FeeAuthority(
        FeeAuthorityStatus.COMPLETE, None, policy, schedule.raw, None, ids, RESOLVER_ID
    )


def _incomplete(reason: str, pdf: RawEvidence | None, changes: RawEvidence | None) -> FeeAuthority:
    return FeeAuthority(FeeAuthorityStatus.INCOMPLETE, reason, None, pdf, changes, (), RESOLVER_ID)


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
        schedule = parse_fee_schedule(pdf)
        parsed_changes = parse_fee_changes(changes)
        result = resolve_fee_authority(decision_at, schedule, parsed_changes)
        return FeeAuthority(
            result.status,
            result.reason,
            result.policy,
            pdf,
            changes,
            result.applicable_change_ids,
            result.resolver_id,
        )
    except FeeAuthorityError as exc:
        return _incomplete(str(exc), None, None)


__all__ = ["FeeAuthority", "FeeAuthorityStatus", "acquire_fee_authority"]
