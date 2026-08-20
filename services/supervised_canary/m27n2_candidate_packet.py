"""M27N.2 -- OFFLINE candidate-evidence packet: full typed-triple reconstruction for M27N/M27N.1.

Fills the exact, disclosed gap :mod:`services.supervised_canary.m27n1_evidence_adapter`'s own
module docstring names ("Known, disclosed gap"): there was no existing validating deserializer
anywhere in this repository that turns persisted evidence into the
``(PhysicalTemperatureProxyProbability, CurrentWeatherForecastEvidence, MarketEconomicsEvidence)``
triple :func:`services.supervised_canary.m27d.select_experimental_candidate` and
:func:`services.supervised_canary.m27n_weather_rehearsal.build_rehearsal` require as
``candidate_inputs``. This module composes that triple, and only that triple, entirely from
existing reviewed validators/builders:

* Event/Market: :mod:`services.supervised_canary.m27n2_evidence_reconstruction` (validated
  snapshot evidence -> typed object), then
  :func:`services.forecasting.daily_temperature.route_daily_temperature` (never hand-constructed).
* Population: :func:`services.forecasting.weather_probability.load_weather_residual_population`.
* Current forecast: :func:`services.forecasting.weather_probability.
  build_current_weather_forecast_evidence`, from an already-parsed, already-frozen-parser-checked
  :class:`services.forecasting.weather_calibration_grib.RawGribEvidence` -- this module never
  invokes wgrib2 or any subprocess itself (that stays in ``scripts/``, which alone may depend on
  the wgrib2 resolver/runner; see the dependency-direction rule stated in
  :mod:`services.market_universe.public_read`'s own module docstring).
* Probability: :func:`services.forecasting.weather_probability.
  physical_temperature_proxy_probability`.
* Orderbook + economics: :func:`services.market_universe.orderbook_snapshot.
  validate_orderbook_snapshot` -> :func:`services.opportunity_engine.authoritative_economics.
  build_authoritative_market_economics`.
* Candidate qualification: :func:`services.supervised_canary.m27d.select_experimental_candidate`
  -- this module never declares qualification itself; it only ever retains the exact
  ``CandidateResult``/``ExperimentalCandidate`` identity that function returns.

Every abstention a frozen builder returns (population, probability, or M27D itself) becomes an
explicit ``ABSTAIN`` packet, never a hard failure and never silently treated as qualification.
Every provenance/tamper/malformed-evidence failure a frozen validator raises propagates unchanged
or becomes :class:`services.supervised_canary.m27n2_evidence_reconstruction.
EvidenceReconstructionError` -- this module never catches or downgrades either.

Packet success states are fixed and exhaustive -- ``state`` is always exactly
``CANDIDATE_QUALIFIED`` or ``ABSTAIN``, and every packet, regardless of state, carries the three
fixed :data:`SUCCESS_MARKERS` strings verbatim: this module has no code path that can ever emit
``READY_TO_TRADE``, ``AUTHORIZED``, ``APPROVED``, ``LIVE_READY``, or ``SAFE_TO_SUBMIT`` -- it
never arms, signs, sends, or submits anything, and a qualified packet is exactly as far from an
order as the ``CandidateResult`` it retains, no closer.

``M27N2CandidatePacket.content_hash`` TRUST MODEL: it is a mutation-detection / deterministic-
identity hash over the packet's own fields -- see :func:`validate_m27n2_candidate_packet`'s
docstring for the full statement. It proves internal self-consistency only, never external
authenticity: a fabricated payload, hashed with the same recipe, still round-trips as "valid".
Every actual provenance/authenticity claim in a packet comes exclusively from the frozen
validators/builders this module calls while *building* the packet, never from the hash itself, and
never from anything this module could re-derive from an already-serialized packet alone.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from services.forecasting.daily_temperature import route_daily_temperature
from services.forecasting.weather_calibration_grib import RawGribEvidence
from services.forecasting.weather_probability import (
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
    WeatherProbabilityAbstention,
    build_current_weather_forecast_evidence,
    load_weather_residual_population,
    physical_temperature_proxy_probability,
)
from services.market_universe.orderbook_snapshot import validate_orderbook_snapshot
from services.market_universe.pricing import PriceLadder
from services.opportunity_engine.authoritative_economics import (
    AuthoritativeMarketEconomicsBinding,
    build_authoritative_market_economics,
)
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.fees import current_event_formula_policy
from services.opportunity_engine.live_economics import MarketEconomicsEvidence
from services.opportunity_engine.live_fees import CurrentSeriesFeeObservation, EventFeeOverride
from services.opportunity_engine.live_fees import resolve_current_fee_regime as _resolve_fee_regime

from .m27d import CandidateResult, CandidateState, select_experimental_candidate
from .m27n2_evidence_reconstruction import (
    EvidenceReconstructionError,
    reconstruct_event,
    reconstruct_market,
)

SCHEMA = "kalsh3.m27n2.candidate-evidence-packet.v1"
SOFTWARE_VERSION = "kalsh3.m27n2.candidate_packet/1"

# Fixed, exhaustive, never-widened success vocabulary. Every packet carries this exact tuple
# regardless of state -- see module docstring. Never READY_TO_TRADE/AUTHORIZED/APPROVED/
# LIVE_READY/SAFE_TO_SUBMIT: this module has no code path that constructs any of those strings.
SUCCESS_MARKERS: tuple[str, ...] = (
    "OFFLINE_CANDIDATE_EVIDENCE_REVALIDATED",
    "REHEARSAL_ONLY",
    "NOT_ORDER_AUTHORIZATION",
)


class CandidatePacketState(StrEnum):
    CANDIDATE_QUALIFIED = "CANDIDATE_QUALIFIED"
    ABSTAIN = "ABSTAIN"


class EvidenceProvenance(StrEnum):
    """Caller-declared, explicit-only classification of the evidence behind one packet.

    Never inferred or defaulted -- exactly like ``M13Fixture.global_halt_clear`` and friends in
    :mod:`services.supervised_canary.m27n1_evidence_adapter`, this module accepts only an actual
    member of this enum, never a bare string, so a synthetic test fixture can never silently be
    represented as production-shaped evidence.
    """

    LOCAL_TEST_FIXTURE = "LOCAL_TEST_FIXTURE"
    PRODUCTION_SHAPED_REPLAY = "PRODUCTION_SHAPED_REPLAY"


@dataclass(frozen=True, slots=True)
class M27N2CandidatePacket:
    """One offline, non-authoritative reconstruction result -- see the module docstring's
    ``content_hash`` trust-model note before treating any field of this class as a security
    claim about the world; only :func:`build_m27n2_candidate_packet`'s own upstream calls into the
    frozen validators/builders are ever the source of truth for that."""

    schema: str
    software_version: str
    created_at: datetime
    evidence_provenance: str
    success_markers: tuple[str, ...]
    state: str
    abstain_reason: str | None
    candidate_id: str | None
    market_ticker: str | None
    event_ticker: str | None
    series_ticker: str | None
    selected_side: str | None
    executable_price: str | None
    research_probability_discrepancy: str | None
    model_identity: str | None
    weather_result_identity: str | None
    forecast_evidence_identity: str | None
    economics_evidence_identity: str | None
    route_source_identity: str | None
    content_hash: str = ""

    def __post_init__(self) -> None:
        payload = self._payload_for_hash()
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        object.__setattr__(self, "content_hash", digest)

    def _payload_for_hash(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "created_at": self.created_at.isoformat(),
            "evidence_provenance": self.evidence_provenance,
            "success_markers": list(self.success_markers),
            "state": self.state,
            "abstain_reason": self.abstain_reason,
            "candidate_id": self.candidate_id,
            "market_ticker": self.market_ticker,
            "event_ticker": self.event_ticker,
            "series_ticker": self.series_ticker,
            "selected_side": self.selected_side,
            "executable_price": self.executable_price,
            "research_probability_discrepancy": self.research_probability_discrepancy,
            "model_identity": self.model_identity,
            "weather_result_identity": self.weather_result_identity,
            "forecast_evidence_identity": self.forecast_evidence_identity,
            "economics_evidence_identity": self.economics_evidence_identity,
            "route_source_identity": self.route_source_identity,
        }

    def to_json(self) -> dict[str, Any]:
        return {**self._payload_for_hash(), "content_hash": self.content_hash}

    @property
    def qualified(self) -> bool:
        return self.state == CandidatePacketState.CANDIDATE_QUALIFIED.value


_PACKET_FIELDS = frozenset(M27N2CandidatePacket.__dataclass_fields__)


@dataclass(frozen=True, slots=True)
class PacketValidation:
    valid: bool
    reason: str | None = None


def _canonical_packet_hash(payload: dict[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_m27n2_candidate_packet(payload: object) -> PacketValidation:
    """Independently re-validate a serialized :class:`M27N2CandidatePacket` payload.

    Never trusts the serialized ``content_hash``/``state``/``success_markers`` in isolation:
    recomputes the canonical content hash and rejects any payload whose stored hash does not
    match, whose keys are unexpected, whose state is not one of the two recognized values, or
    whose success markers are not exactly the fixed reviewed set.

    ``content_hash`` TRUST MODEL -- read before calling this in any context where the answer
    matters: a ``PacketValidation(valid=True, ...)`` result here proves only that the payload is
    internally self-consistent (its ``content_hash`` matches an unkeyed SHA-256 over its own
    other fields) and therefore has not been edited *after* that hash was computed. It is a
    mutation-detection/deterministic-identity check, nothing more -- it is NOT a signature, NOT a
    provenance credential, and NOT proof that any field's underlying claim (an event/market really
    existed with that identity, a GRIB extraction really came from NOAA, a model really trained on
    that population, a candidate really passed M27D) is true. A payload can be entirely
    fabricated, hashed correctly with this exact recipe, and still pass this function. The ONLY
    place external authenticity for those claims is ever established is upstream, inside
    :func:`build_m27n2_candidate_packet`'s own calls into the frozen validators/builders it
    composes (:func:`~services.supervised_canary.m27n2_evidence_reconstruction.reconstruct_event`
    / ``reconstruct_market``, :func:`~services.forecasting.daily_temperature.
    route_daily_temperature`, :func:`~services.forecasting.weather_probability.
    load_weather_residual_population`, :func:`~services.forecasting.weather_probability.
    build_current_weather_forecast_evidence`, :func:`~services.market_universe.orderbook_snapshot.
    validate_orderbook_snapshot`, :func:`~services.opportunity_engine.authoritative_economics.
    build_authoritative_market_economics`, :func:`~services.supervised_canary.m27d.
    select_experimental_candidate`) -- this function never re-runs any of those, and cannot: it
    only ever sees the already-serialized packet, not the raw evidence behind it. Callers who need
    to know whether a *specific* packet's claims are backed by real evidence must independently
    re-run :func:`build_m27n2_candidate_packet` against the same retained raw evidence and compare
    results -- recomputing this hash from a payload proves nothing about that.
    """
    if not isinstance(payload, dict):
        return PacketValidation(False, "candidate packet payload is not an object")
    if set(payload) != _PACKET_FIELDS:
        return PacketValidation(False, "candidate packet has unexpected or missing fields")
    if payload.get("schema") != SCHEMA:
        return PacketValidation(False, "candidate packet schema mismatch")
    stored_hash = payload.get("content_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        return PacketValidation(False, "candidate packet content hash missing")
    if _canonical_packet_hash(payload) != stored_hash:
        return PacketValidation(False, "candidate packet content hash does not match its contents")
    if payload.get("state") not in {
        CandidatePacketState.CANDIDATE_QUALIFIED.value,
        CandidatePacketState.ABSTAIN.value,
    }:
        return PacketValidation(False, "candidate packet state is not recognized")
    if payload.get("evidence_provenance") not in {
        EvidenceProvenance.LOCAL_TEST_FIXTURE.value,
        EvidenceProvenance.PRODUCTION_SHAPED_REPLAY.value,
    }:
        return PacketValidation(False, "candidate packet evidence_provenance is not recognized")
    raw_markers = payload.get("success_markers")
    # Require an actual list/tuple -- never a bare string (which is itself iterable character-by-
    # character and must not be silently treated as a collection of markers) and never any other
    # non-iterable-or-wrong-shape value (int, bool, None, dict, ...), so this never reaches
    # tuple(...) with something that would raise TypeError instead of failing closed.
    if not isinstance(raw_markers, list | tuple):
        return PacketValidation(
            False, "candidate packet success markers is not a list of marker strings"
        )
    if tuple(raw_markers) != SUCCESS_MARKERS:
        return PacketValidation(
            False, "candidate packet success markers are not the fixed reviewed set"
        )
    return PacketValidation(True, None)


def reconstruct_economics(
    *,
    market_snapshot_payload: object,
    orderbook_snapshot_payload: object,
    expected_market_ticker: str,
    expected_event_ticker: str,
    series_ticker: str,
    market_source_id: str,
    ladder: PriceLadder,
    orderbook_source_id: str,
    series_fee_payload: Mapping[str, Any],
    event_fee_payload: Mapping[str, Any],
    requested_quantity: Decimal,
    economics_observed_at: datetime,
    now: datetime,
) -> tuple[MarketEconomicsEvidence, AuthoritativeMarketEconomicsBinding]:
    """Reconstruct authoritative market economics from validated orderbook + market snapshot
    evidence, recomputing the fee regime through its own existing reviewed constructors rather
    than accepting any caller-supplied fee/regime claim.

    ``series_fee_payload`` is untrusted caller-supplied evidence like every other payload this
    function accepts: :class:`~services.opportunity_engine.live_fees.CurrentSeriesFeeObservation`
    is the existing reviewed parser's own canonical series identity for that evidence (populated
    directly from its ``ticker`` field), and it is cross-checked against the exact expected
    ``series_ticker`` immediately after parsing, before the resulting fee regime can influence
    economics at all. A fee observation for a different (even a valid, unrelated) series must
    never silently supply this market's fee policy.

    Raises :class:`EvidenceReconstructionError` (fail closed) if the orderbook snapshot does not
    independently validate, if either fee payload fails its own reviewed parser, if the fee
    evidence's own series ticker does not match ``series_ticker``, or wraps
    :class:`services.opportunity_engine.domain.OpportunityError` from
    :func:`build_authoritative_market_economics` the same way -- every failure path through this
    function's public boundary raises the same exception type, never a bare
    :class:`~services.opportunity_engine.domain.OpportunityError` leaking past it.
    """
    orderbook_validation = validate_orderbook_snapshot(
        orderbook_snapshot_payload, expected_ticker=expected_market_ticker, now=now
    )
    if not orderbook_validation.succeeded or orderbook_validation.observed_at is None:
        raise EvidenceReconstructionError(
            f"orderbook snapshot evidence did not validate: "
            f"{orderbook_validation.classification} ({orderbook_validation.reason})"
        )
    raw_orderbook = orderbook_validation.raw_orderbook_for_economics()

    try:
        series_observation = CurrentSeriesFeeObservation.parse(
            dict(series_fee_payload), observed_at=economics_observed_at
        )
    except OpportunityError as exc:
        raise EvidenceReconstructionError(f"series fee evidence failed to parse: {exc}") from exc
    if series_observation.series_ticker != series_ticker:
        raise EvidenceReconstructionError(
            "series fee evidence ticker does not match the expected series: "
            f"expected {series_ticker!r}, fee evidence declared "
            f"{series_observation.series_ticker!r}"
        )
    try:
        event_override = EventFeeOverride.parse(dict(event_fee_payload))
    except OpportunityError as exc:
        raise EvidenceReconstructionError(f"event fee override failed to parse: {exc}") from exc
    fee_regime = _resolve_fee_regime(series_observation, event_override)
    fee_policy = current_event_formula_policy(
        fee_type=fee_regime.fee_type, fee_multiplier=fee_regime.fee_multiplier
    )

    try:
        return build_authoritative_market_economics(
            snapshot_payload=market_snapshot_payload,
            expected_market_ticker=expected_market_ticker,
            expected_event_ticker=expected_event_ticker,
            series_ticker=series_ticker,
            market_source_id=market_source_id,
            raw_orderbook=raw_orderbook,
            ladder=ladder,
            orderbook_source_id=orderbook_source_id,
            orderbook_observed_at=orderbook_validation.observed_at,
            series_fee_observation_id=fee_regime.series_observation_id,
            resolved_fee_regime_id=fee_regime.regime_id,
            event_fee_hash=fee_regime.event_metadata_hash,
            fee_policy=fee_policy,
            fee_regime=fee_regime,
            requested_quantity=requested_quantity,
            economics_observed_at=economics_observed_at,
        )
    except OpportunityError as exc:
        raise EvidenceReconstructionError(
            f"authoritative economics reconstruction failed: {exc}"
        ) from exc


def _abstain_packet(
    now: datetime, provenance: EvidenceProvenance, reason: str
) -> M27N2CandidatePacket:
    return M27N2CandidatePacket(
        schema=SCHEMA,
        software_version=SOFTWARE_VERSION,
        created_at=now,
        evidence_provenance=provenance.value,
        success_markers=SUCCESS_MARKERS,
        state=CandidatePacketState.ABSTAIN.value,
        abstain_reason=reason,
        candidate_id=None,
        market_ticker=None,
        event_ticker=None,
        series_ticker=None,
        selected_side=None,
        executable_price=None,
        research_probability_discrepancy=None,
        model_identity=None,
        weather_result_identity=None,
        forecast_evidence_identity=None,
        economics_evidence_identity=None,
        route_source_identity=None,
    )


def build_m27n2_candidate_packet(
    *,
    now: datetime,
    evidence_provenance: EvidenceProvenance,
    event_snapshot_payload: object,
    market_snapshot_payload: object,
    orderbook_snapshot_payload: object,
    expected_market_ticker: str,
    expected_event_ticker: str,
    series_ticker: str,
    raw_grib_evidence: RawGribEvidence,
    forecast_record_number: int,
    population_artifact_payload: Mapping[str, Any],
    population_exact_midpoint_seconds: int,
    population_training_start: date,
    population_training_end: date,
    market_source_id: str,
    ladder: PriceLadder,
    orderbook_source_id: str,
    series_fee_payload: Mapping[str, Any],
    event_fee_payload: Mapping[str, Any],
    requested_quantity: Decimal,
    economics_observed_at: datetime,
) -> M27N2CandidatePacket:
    """Reconstruct the full typed evidence triple end to end and hand it to the existing,
    unmodified :func:`services.supervised_canary.m27d.select_experimental_candidate`.

    This function itself never decides candidate qualification -- it only ever retains the exact
    ``CandidateResult``/``ExperimentalCandidate`` identity M27D returns. Every population,
    probability, or M27D abstention becomes an explicit ``ABSTAIN`` packet; every provenance or
    tamper failure raises (fail closed) rather than being papered over. No network, no
    credentials, no signer, no SQLite store, no order/cancel/amend capability anywhere in this
    module -- see :data:`SUCCESS_MARKERS` and the module docstring.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise EvidenceReconstructionError("packet clock must be timezone-aware")
    if not isinstance(evidence_provenance, EvidenceProvenance):
        raise EvidenceReconstructionError(
            "evidence_provenance must be an explicit EvidenceProvenance member"
        )

    event = reconstruct_event(event_snapshot_payload, expected_ticker=expected_event_ticker)
    market = reconstruct_market(
        market_snapshot_payload,
        expected_ticker=expected_market_ticker,
        expected_event_ticker=expected_event_ticker,
    )
    route = route_daily_temperature(market, event)

    population = load_weather_residual_population(
        population_artifact_payload,
        exact_midpoint_seconds=population_exact_midpoint_seconds,
        training_start=population_training_start,
        training_end=population_training_end,
    )
    if isinstance(population, WeatherProbabilityAbstention):
        return _abstain_packet(
            now,
            evidence_provenance,
            f"population abstained: {population.reason.value} ({population.detail})",
        )

    current: CurrentWeatherForecastEvidence = build_current_weather_forecast_evidence(
        raw_grib_evidence, record_number=forecast_record_number
    )

    probability = physical_temperature_proxy_probability(
        route=route, population=population, current=current
    )
    if isinstance(probability, WeatherProbabilityAbstention):
        return _abstain_packet(
            now,
            evidence_provenance,
            f"probability abstained: {probability.reason.value} ({probability.detail})",
        )
    typed_probability: PhysicalTemperatureProxyProbability = probability

    economics, _binding = reconstruct_economics(
        market_snapshot_payload=market_snapshot_payload,
        orderbook_snapshot_payload=orderbook_snapshot_payload,
        expected_market_ticker=expected_market_ticker,
        expected_event_ticker=expected_event_ticker,
        series_ticker=series_ticker,
        market_source_id=market_source_id,
        ladder=ladder,
        orderbook_source_id=orderbook_source_id,
        series_fee_payload=series_fee_payload,
        event_fee_payload=event_fee_payload,
        requested_quantity=requested_quantity,
        economics_observed_at=economics_observed_at,
        now=now,
    )

    candidate_result: CandidateResult = select_experimental_candidate(
        ((typed_probability, current, economics),), now=now
    )
    if candidate_result.state is CandidateState.ABSTAIN or candidate_result.selected is None:
        return _abstain_packet(now, evidence_provenance, candidate_result.reason)

    candidate = candidate_result.selected
    return M27N2CandidatePacket(
        schema=SCHEMA,
        software_version=SOFTWARE_VERSION,
        created_at=now,
        evidence_provenance=evidence_provenance.value,
        success_markers=SUCCESS_MARKERS,
        state=CandidatePacketState.CANDIDATE_QUALIFIED.value,
        abstain_reason=None,
        candidate_id=candidate.candidate_id,
        market_ticker=candidate.market_ticker,
        event_ticker=candidate.event_ticker,
        series_ticker=candidate.series_ticker,
        selected_side=candidate.selected_side.value,
        executable_price=str(candidate.executable_price),
        research_probability_discrepancy=str(candidate.research_probability_discrepancy),
        model_identity=candidate.eligibility.model_identity,
        weather_result_identity=candidate.eligibility.weather_result_identity,
        forecast_evidence_identity=candidate.eligibility.forecast_evidence_identity,
        economics_evidence_identity=candidate.economics_evidence_identity,
        route_source_identity=route.source_identity,
    )
