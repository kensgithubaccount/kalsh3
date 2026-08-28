"""Immutable, research-only receipts for predictions made before outcomes exist.

This module composes the canonical :class:`services.forecasting.models.Forecast`.
It adds the forward-evidence identity and a create-only archive, but it does not
score outcomes, promote models, or authorize capital.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from services.forecasting.models import Forecast
from services.historical_replay.archive import stable_hash


class ProspectiveReceiptError(ValueError):
    """A prospective receipt or its immutable archive is invalid."""


SCHEMA_VERSION = "kalsh3.forward-reality.prospective-prediction-receipt.v1"
PROVENANCE = "PROSPECTIVE"
PUBLICATION_SCHEMA_VERSION = "kalsh3.forward-reality.prospective-receipt-publication.v1"
PUBLICATION_POLICY = "issuer-observed-archive-publication-utc-v1"
_PUBLICATION_ISSUANCE_CAPABILITY = object()
_ISSUER_KEY_SUFFIX = ".prospective-receipt-issuer-key"


def _utc_now() -> datetime:
    """Issuer clock seam; production publication uses the runtime UTC clock."""
    return datetime.now(UTC)


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def _exact_string(value: object, field: str, *, allow_none: bool = False) -> str | None:
    if allow_none and value is None:
        return None
    if type(value) is not str or not value:
        raise ProspectiveReceiptError(f"{field} must be a non-empty exact string")
    return value


def _exact_timestamp(value: object, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ProspectiveReceiptError(f"{field} must be a timezone-aware exact datetime")
    return value


def _canonical_utc(value: object, field: str) -> datetime:
    timestamp = _exact_timestamp(value, field)
    if timestamp.utcoffset() != timedelta(0):
        raise ProspectiveReceiptError(f"{field} must use canonical UTC")
    return timestamp.astimezone(UTC)


def _exact_string_tuple(value: object, field: str) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(item) is not str or not item for item in value):
        raise ProspectiveReceiptError(f"{field} must be a tuple of non-empty exact strings")
    return value


@dataclass(frozen=True, slots=True)
class ProspectivePredictionReceipt:
    """The locked envelope for one genuinely prospective canonical forecast."""

    schema_version: str
    receipt_id: str
    candidate_id: str
    model_id: str
    model_version: str
    calibrator_id: str
    feature_schema_id: str
    code_identity: str
    market_ticker: str
    event_ticker: str
    underlying_event_id: str
    decision_at: datetime
    forecast_created_at: datetime
    raw_probability: Decimal | None
    calibrated_probability: Decimal | None
    lower_probability: Decimal | None
    upper_probability: Decimal | None
    uncertainty_method: str | None
    abstained: bool
    abstention_reason: str | None
    evidence_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    market_snapshot_id: str | None
    market_reference: object | None
    prediction_horizon_seconds: int
    canonical_forecast_id: str
    canonical_forecast_content_hash: str
    provenance: str
    research_only: bool
    production_influence: Decimal
    content_hash: str

    @classmethod
    def from_forecast(
        cls,
        forecast: Forecast,
        *,
        candidate_id: str,
        calibrator_id: str,
        feature_schema_id: str,
        code_identity: str,
        event_ticker: str,
        underlying_event_id: str,
        evidence_ids: tuple[str, ...],
        source_ids: tuple[str, ...],
        market_snapshot_id: str | None,
    ) -> ProspectivePredictionReceipt:
        if type(forecast) is not Forecast:
            raise ProspectiveReceiptError(
                "prospective receipt requires the canonical Forecast type"
            )
        if forecast.replay_time is not None:
            raise ProspectiveReceiptError(
                "historical replay forecasts cannot become prospective receipts"
            )
        if forecast.production_influence != Decimal("0"):
            raise ProspectiveReceiptError("prospective receipt must have zero production influence")
        decision_at = _exact_timestamp(forecast.issued_at, "decision_at")
        created_at = _exact_timestamp(forecast.created_at, "forecast_created_at")
        if created_at < decision_at:
            raise ProspectiveReceiptError("forecast creation cannot precede its decision timestamp")
        if forecast.target_resolution_time <= decision_at:
            raise ProspectiveReceiptError(
                "prospective forecast horizon must end after decision time"
            )
        if type(forecast.horizon_seconds) is not int or forecast.horizon_seconds <= 0:
            raise ProspectiveReceiptError("prediction horizon must be a positive exact integer")
        if forecast.target_resolution_time != decision_at + timedelta(
            seconds=forecast.horizon_seconds
        ):
            raise ProspectiveReceiptError(
                "prediction horizon does not match target resolution time"
            )
        candidate = _exact_string(candidate_id, "candidate_id")
        calibrator = _exact_string(calibrator_id, "calibrator_id")
        feature_schema = _exact_string(feature_schema_id, "feature_schema_id")
        code = _exact_string(code_identity, "code_identity")
        event = _exact_string(event_ticker, "event_ticker")
        dependency = _exact_string(underlying_event_id, "underlying_event_id")
        evidence = _exact_string_tuple(evidence_ids, "evidence_ids")
        sources = _exact_string_tuple(source_ids, "source_ids")
        snapshot = _exact_string(market_snapshot_id, "market_snapshot_id", allow_none=True)
        if forecast.market_reference is not None and snapshot is None:
            raise ProspectiveReceiptError("market reference requires its exact snapshot identity")
        abstention_reason = (
            str(forecast.abstention_reason) if forecast.abstention_reason is not None else None
        )
        abstained = forecast.abstention_reason is not None
        if abstained != (abstention_reason is not None):
            raise ProspectiveReceiptError("abstention state is inconsistent")
        canonical_forecast_content_hash = stable_hash(_jsonable(forecast))
        values: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": candidate,
            "model_id": forecast.model_id,
            "model_version": forecast.model_version,
            "calibrator_id": calibrator,
            "feature_schema_id": feature_schema,
            "code_identity": code,
            "market_ticker": forecast.market_ticker,
            "event_ticker": event,
            "underlying_event_id": dependency,
            "decision_at": decision_at,
            "forecast_created_at": created_at,
            "raw_probability": forecast.raw_probability,
            "calibrated_probability": forecast.calibrated_probability,
            "lower_probability": forecast.lower_probability,
            "upper_probability": forecast.upper_probability,
            "uncertainty_method": forecast.uncertainty_method,
            "abstained": abstained,
            "abstention_reason": abstention_reason,
            "evidence_ids": evidence,
            "source_ids": sources,
            "market_snapshot_id": snapshot,
            "market_reference": forecast.market_reference,
            "prediction_horizon_seconds": forecast.horizon_seconds,
            "canonical_forecast_id": forecast.forecast_id,
            "canonical_forecast_content_hash": canonical_forecast_content_hash,
            "provenance": PROVENANCE,
            "research_only": True,
            "production_influence": Decimal("0"),
        }
        content_hash = stable_hash(_jsonable(values))
        return cls(
            **values,  # type: ignore[arg-type]
            receipt_id=content_hash,
            content_hash=content_hash,
        )

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.provenance != PROVENANCE:
            raise ProspectiveReceiptError("receipt schema or provenance is not prospective")
        if self.research_only is not True or self.production_influence != Decimal("0"):
            raise ProspectiveReceiptError(
                "prospective receipts are research-only with zero influence"
            )
        if type(self.abstained) is not bool:
            raise ProspectiveReceiptError("abstention state must be an exact boolean")
        if (self.abstention_reason is None) != (not self.abstained):
            raise ProspectiveReceiptError("abstention reason does not match abstention state")
        expected = stable_hash(_jsonable(self._identity_values()))
        if self.receipt_id != expected or self.content_hash != expected:
            raise ProspectiveReceiptError("receipt content hash does not match immutable fields")

    def _identity_values(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name)
            for field in dataclasses.fields(self)
            if field.name not in {"receipt_id", "content_hash"}
        }

    def to_payload(self) -> dict[str, object]:
        return _jsonable(dataclasses.asdict(self))  # type: ignore[return-value]

    def to_bytes(self) -> bytes:
        return (
            json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()


@dataclass(frozen=True, slots=True, init=False)
class ProspectiveReceiptPublication:
    """Issuer-observed fact that a receipt entered this trusted archive."""

    schema_version: str
    receipt_id: str
    receipt_content_hash: str
    archive_id: str
    published_at: datetime
    policy: str
    research_only: bool
    production_influence: Decimal
    publication_id: str
    issuer_mac: str

    def __init__(
        self,
        *,
        schema_version: str,
        receipt_id: str,
        receipt_content_hash: str,
        archive_id: str,
        published_at: datetime,
        policy: str,
        research_only: bool,
        production_influence: Decimal,
        publication_id: str,
        issuer_mac: str,
        _capability: object | None = None,
        _issuer_key: bytes | None = None,
    ) -> None:
        if _capability is not _PUBLICATION_ISSUANCE_CAPABILITY:
            raise ProspectiveReceiptError("publication requires reviewed issuer capability")
        if _issuer_key is None or type(_issuer_key) is not bytes or len(_issuer_key) < 32:
            raise ProspectiveReceiptError("publication requires issuer key")
        if schema_version != PUBLICATION_SCHEMA_VERSION:
            raise ProspectiveReceiptError("publication schema is not recognized")
        published = _canonical_utc(published_at, "publication timestamp")
        if policy != PUBLICATION_POLICY:
            raise ProspectiveReceiptError("publication policy is not recognized")
        if research_only is not True or production_influence != Decimal("0"):
            raise ProspectiveReceiptError(
                "prospective publication is research-only with zero influence"
            )
        values = {
            "schema_version": schema_version,
            "receipt_id": receipt_id,
            "receipt_content_hash": receipt_content_hash,
            "archive_id": archive_id,
            "published_at": published,
            "policy": policy,
            "research_only": research_only,
            "production_influence": production_influence,
        }
        expected_id = stable_hash(_jsonable(values))
        expected_mac = self._mac(values, _issuer_key)
        if publication_id != expected_id or not hmac.compare_digest(issuer_mac, expected_mac):
            raise ProspectiveReceiptError("publication issuer seal is invalid")
        issued_values = {**values, "publication_id": publication_id, "issuer_mac": issuer_mac}
        for name, value in issued_values.items():
            object.__setattr__(self, name, value)

    def _identity_values(self) -> dict[str, object]:
        return {
            field.name: getattr(self, field.name)
            for field in dataclasses.fields(self)
            if field.name not in {"publication_id", "issuer_mac"}
        }

    @staticmethod
    def _mac(values: dict[str, object], issuer_key: bytes) -> str:
        identity = json.dumps(_jsonable(values), sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(issuer_key, identity, hashlib.sha256).hexdigest()

    def to_bytes(self) -> bytes:
        return (
            json.dumps(_jsonable(dataclasses.asdict(self)), sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()

    @classmethod
    def from_bytes(cls, encoded: bytes) -> ProspectiveReceiptPublication:
        raise ProspectiveReceiptError("publication reconstruction requires issuer validation")

    @classmethod
    def _from_payload(cls, payload: object, *, issuer_key: bytes) -> ProspectiveReceiptPublication:
        if type(payload) is not dict:
            raise ProspectiveReceiptError("outcome cannot bind an invalid publication")
        try:
            values = dict(payload)
            values["published_at"] = datetime.fromisoformat(values["published_at"])
            values["production_influence"] = Decimal(values["production_influence"])
            return cls(
                **values,
                _capability=_PUBLICATION_ISSUANCE_CAPABILITY,
                _issuer_key=issuer_key,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise ProspectiveReceiptError("outcome cannot bind an invalid publication") from exc


class ProspectiveReceiptStore:
    """Create-only archive with an issuer-observed publication-time boundary."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _issuer_key(self) -> bytes:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root.with_name(self.root.name + _ISSUER_KEY_SUFFIX)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            key = path.read_bytes()
            if len(key) < 32:
                raise ProspectiveReceiptError("issuer key is invalid") from None
            return key
        key = secrets.token_bytes(32)
        try:
            os.write(fd, key)
            os.fsync(fd)
        finally:
            os.close(fd)
        return key

    def publish(self, receipt: ProspectivePredictionReceipt) -> None:
        path = self.root / f"{receipt.receipt_id}.json"
        publication_path = self.root / f"{receipt.receipt_id}.publication.json"
        encoded = receipt.to_bytes()
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ProspectiveReceiptError(
                    "receipt identity already exists with conflicting content"
                ) from None
        else:
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise ProspectiveReceiptError("receipt archive write made no progress")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)

        key = self._issuer_key()
        if publication_path.is_file():
            try:
                publication = self._read_publication(publication_path, key)
            except ProspectiveReceiptError:
                raise ProspectiveReceiptError(
                    "publication identity already exists with conflicting content"
                ) from None
            if publication.receipt_id != receipt.receipt_id:
                raise ProspectiveReceiptError("publication is bound to a different receipt")
            if publication.published_at >= receipt.decision_at + timedelta(
                seconds=receipt.prediction_horizon_seconds
            ):
                raise ProspectiveReceiptError(
                    "trusted publication must precede the forecast target resolution"
                )
            return
        publication = self._new_publication(receipt, key)
        publication_encoded = publication.to_bytes()
        try:
            fd = os.open(publication_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if publication_path.read_bytes() != publication_encoded:
                raise ProspectiveReceiptError(
                    "publication identity already exists with conflicting content"
                ) from None
            return
        try:
            view = memoryview(publication_encoded)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise ProspectiveReceiptError("publication archive write made no progress")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _new_publication(
        self, receipt: ProspectivePredictionReceipt, issuer_key: bytes
    ) -> ProspectiveReceiptPublication:
        published_at = _utc_now()
        if type(published_at) is not datetime or published_at.tzinfo is None:
            raise ProspectiveReceiptError("issuer clock must return a timezone-aware datetime")
        published_at = _canonical_utc(published_at, "publication timestamp")
        if published_at >= receipt.decision_at + timedelta(
            seconds=receipt.prediction_horizon_seconds
        ):
            raise ProspectiveReceiptError(
                "trusted publication must precede the forecast target resolution"
            )
        values: dict[str, object] = {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "receipt_id": receipt.receipt_id,
            "receipt_content_hash": receipt.content_hash,
            "archive_id": str(self.root.resolve()),
            "published_at": published_at,
            "policy": PUBLICATION_POLICY,
            "research_only": True,
            "production_influence": Decimal("0"),
        }
        publication_id = stable_hash(_jsonable(values))
        issuer_mac = ProspectiveReceiptPublication._mac(values, issuer_key)
        return ProspectiveReceiptPublication(
            schema_version=cast(str, values["schema_version"]),
            receipt_id=cast(str, values["receipt_id"]),
            receipt_content_hash=cast(str, values["receipt_content_hash"]),
            archive_id=cast(str, values["archive_id"]),
            published_at=cast(datetime, values["published_at"]),
            policy=cast(str, values["policy"]),
            research_only=cast(bool, values["research_only"]),
            production_influence=cast(Decimal, values["production_influence"]),
            publication_id=publication_id,
            issuer_mac=issuer_mac,
            _capability=_PUBLICATION_ISSUANCE_CAPABILITY,
            _issuer_key=issuer_key,
        )

    def _read_publication(self, path: Path, issuer_key: bytes) -> ProspectiveReceiptPublication:
        try:
            payload = json.loads(path.read_bytes())
        except (json.JSONDecodeError, OSError) as exc:
            raise ProspectiveReceiptError("outcome cannot bind an invalid publication") from exc
        return ProspectiveReceiptPublication._from_payload(payload, issuer_key=issuer_key)

    def require_frozen(
        self, receipt: ProspectivePredictionReceipt, outcome_available_at: datetime
    ) -> None:
        """Permit a later outcome binder to proceed only after this receipt exists."""
        if type(outcome_available_at) is not datetime or outcome_available_at.tzinfo is None:
            raise ProspectiveReceiptError("outcome availability must be a timezone-aware datetime")
        path = self.root / f"{receipt.receipt_id}.json"
        if not path.is_file() or path.read_bytes() != receipt.to_bytes():
            raise ProspectiveReceiptError("outcome cannot bind an unpublished or changed receipt")
        publication_path = self.root / f"{receipt.receipt_id}.publication.json"
        if not publication_path.is_file():
            raise ProspectiveReceiptError("outcome cannot bind an unpublished receipt")
        issuer_key = self._issuer_key()
        publication = self._read_publication(publication_path, issuer_key)
        if publication.receipt_id != receipt.receipt_id:
            raise ProspectiveReceiptError("publication is bound to a different receipt")
        if publication.receipt_content_hash != receipt.content_hash:
            raise ProspectiveReceiptError("publication is bound to changed receipt content")
        target_resolution = receipt.decision_at + timedelta(
            seconds=receipt.prediction_horizon_seconds
        )
        if publication.published_at >= target_resolution:
            raise ProspectiveReceiptError(
                "trusted publication must precede the forecast target resolution"
            )
        if publication_path.read_bytes() != publication.to_bytes():
            raise ProspectiveReceiptError("outcome cannot bind a changed publication")
        if outcome_available_at <= publication.published_at:
            raise ProspectiveReceiptError("outcome is not later than trusted publication")


class ProspectiveOutcomeBoundary:
    """Chronology-only handoff; outcome authority and scoring remain elsewhere."""

    @staticmethod
    def require_frozen(
        store: ProspectiveReceiptStore,
        receipt: ProspectivePredictionReceipt,
        outcome_available_at: datetime,
    ) -> None:
        store.require_frozen(receipt, outcome_available_at)
