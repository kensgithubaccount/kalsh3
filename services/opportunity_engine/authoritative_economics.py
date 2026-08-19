"""M27A authoritative economics construction + binding -- the "expected side" of M27I's rules
currentness proof.

Gemini delta repair: the legacy path --

    caller-supplied market_rules_hash -> normalize_live_orderbook(...) ->
    BookObservation.market_rules_hash -> MarketEconomicsEvidence.market_rules_hash

-- never proves that value came from a real market at all: a caller can put today's M27J hash
into economics and trivially get ``H == H``. That legacy path remains valid for research (see
:mod:`services.opportunity_engine.live_economics`, unmodified here) but is not sufficient for
live-canary rules authority.

This module adds an AUTHORITATIVE construction path, :func:`build_authoritative_market_economics`,
whose caller cannot supply ``market_rules_hash``/``market_metadata_hash``/``market_ticker``/
``event_ticker``/``market_observed_at`` at all -- every one of those is derived exclusively from
an independently-validated :class:`services.market_universe.market_snapshot.
AuthoritativeMarketSnapshot` acquired via the same reviewed public GET boundary M27J uses. It
produces, in one atomic operation, both the ordinary :class:`MarketEconomicsEvidence` and a
separate, independently re-validatable :class:`AuthoritativeMarketEconomicsBinding` that a
consumer (M27I) can use to prove "this exact economics evidence came from this exact,
independently re-parsed live market snapshot" -- never merely trusting
``economics.market_rules_hash == snapshot.rules_hash`` in isolation, since a caller could
construct the two separately.

Trust model: neither the snapshot nor this binding is server-signed. SHA-256 proves the recorded
bytes were not altered after acquisition; origin authority comes only from the reviewed
acquisition boundary (:mod:`services.market_universe.public_read`) having made the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from services.market_universe.market_snapshot import FRESHNESS, validate_market_snapshot
from services.market_universe.pricing import PriceLadder
from services.opportunity_engine.books import OutcomeSide, walk_depth
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.fees import FeePolicy
from services.opportunity_engine.live_economics import (
    BookObservation,
    MarketEconomicsEvidence,
    MarketEconomicsReplayInput,
    TakerCost,
    normalize_live_orderbook,
    taker_cost,
)
from services.opportunity_engine.live_fees import ResolvedFeeRegime

SCHEMA = "kalsh3.m27a.authoritative-market-economics-binding.v1"

# The maximum age a market snapshot may have relative to the economics evaluation it backs. Reuses
# the same single reviewed 30-second bound M27J's own current-side freshness uses (see
# services.market_universe.market_snapshot.FRESHNESS and services.supervised_canary.m27d.
# MAX_BOOK_AGE) rather than inventing a second window.
MAX_ACQUISITION_SKEW = FRESHNESS


@dataclass(frozen=True, slots=True)
class AuthoritativeMarketEconomicsBinding:
    schema: str
    economics_evidence_id: str
    market_ticker: str
    event_ticker: str
    market_rules_hash: str
    market_metadata_hash: str
    market_observed_at: datetime
    economics_observed_at: datetime
    orderbook_source_hash: str
    price_range_hash: str
    expected_snapshot: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "economics_evidence_id": self.economics_evidence_id,
            "market_ticker": self.market_ticker,
            "event_ticker": self.event_ticker,
            "market_rules_hash": self.market_rules_hash,
            "market_metadata_hash": self.market_metadata_hash,
            "market_observed_at": self.market_observed_at.isoformat(),
            "economics_observed_at": self.economics_observed_at.isoformat(),
            "orderbook_source_hash": self.orderbook_source_hash,
            "price_range_hash": self.price_range_hash,
            "expected_snapshot": self.expected_snapshot,
        }


_BINDING_FIELDS = frozenset(AuthoritativeMarketEconomicsBinding.__dataclass_fields__)


def build_authoritative_market_economics(
    *,
    snapshot_payload: object,
    expected_market_ticker: str,
    expected_event_ticker: str,
    series_ticker: str,
    market_source_id: str,
    raw_orderbook: dict[str, Any],
    ladder: PriceLadder,
    orderbook_source_id: str,
    orderbook_observed_at: datetime,
    series_fee_observation_id: str,
    resolved_fee_regime_id: str,
    event_fee_hash: str,
    fee_policy: FeePolicy,
    fee_regime: ResolvedFeeRegime,
    requested_quantity: Decimal,
    economics_observed_at: datetime,
) -> tuple[MarketEconomicsEvidence, AuthoritativeMarketEconomicsBinding]:
    """Build authoritative :class:`MarketEconomicsEvidence` + its binding in one operation.

    ``market_rules_hash``/``market_metadata_hash``/``market_ticker``/``event_ticker``/
    ``market_observed_at`` are never accepted as parameters here -- there is no way for a caller
    to substitute a rules string. Every one of those is derived exclusively from independently
    re-validating ``snapshot_payload`` through
    :func:`services.market_universe.market_snapshot.validate_market_snapshot`, which reruns the
    canonical :meth:`services.market_universe.domain.Market.parse` against the exact retained raw
    bytes.

    Raises :class:`services.opportunity_engine.domain.OpportunityError` (fail closed) if the
    snapshot is invalid, or if the snapshot's acquisition time is not at or before
    ``economics_observed_at`` within :data:`MAX_ACQUISITION_SKEW` -- a market snapshot acquired
    strictly after the economics evaluation it is supposed to back, or one acquired materially
    earlier, is never accepted as "rules-at-economics-evaluation" authority.
    """
    snapshot_result = validate_market_snapshot(
        snapshot_payload,
        expected_ticker=expected_market_ticker,
        expected_event_ticker=expected_event_ticker,
    )
    if not snapshot_result.succeeded:
        raise OpportunityError(
            f"authoritative market snapshot invalid: {snapshot_result.classification} "
            f"({snapshot_result.reason})"
        )
    if not isinstance(snapshot_payload, dict):  # narrowed by validate_market_snapshot success
        raise OpportunityError("authoritative market snapshot payload is not an object")
    market_observed_at = snapshot_result.observed_at
    rules_hash = snapshot_result.rules_hash
    metadata_hash = snapshot_result.metadata_hash
    market_ticker = snapshot_result.ticker
    event_ticker = snapshot_result.event_ticker
    if (
        market_observed_at is None
        or rules_hash is None
        or metadata_hash is None
        or market_ticker is None
        or event_ticker is None
    ):  # pragma: no cover - PASS always carries all of these
        raise OpportunityError("authoritative market snapshot validation result incomplete")

    economics_at = _utc(economics_observed_at)
    if market_observed_at > economics_at:
        raise OpportunityError(
            "authoritative market snapshot was acquired after the economics evaluation it backs"
        )
    if economics_at - market_observed_at > MAX_ACQUISITION_SKEW:
        raise OpportunityError(
            "authoritative market snapshot acquisition/evaluation skew exceeds the reviewed bound"
        )

    observation = normalize_live_orderbook(
        raw_orderbook,
        ticker=market_ticker,
        ladder=ladder,
        source_id=orderbook_source_id,
        observed_at=orderbook_observed_at,
        market_rules_hash=rules_hash,
    )
    replay_input = MarketEconomicsReplayInput(observation, ladder, fee_regime, fee_policy)
    yes_cost = _side_cost(observation, OutcomeSide.YES, requested_quantity, fee_policy)
    no_cost = _side_cost(observation, OutcomeSide.NO, requested_quantity, fee_policy)

    values: dict[str, Any] = dict(
        market_ticker=market_ticker,
        event_ticker=event_ticker,
        series_ticker=series_ticker,
        market_source_id=market_source_id,
        market_rules_hash=rules_hash,
        market_metadata_hash=metadata_hash,
        price_range_hash=observation.price_range_hash,
        event_fee_hash=event_fee_hash,
        series_fee_observation_id=series_fee_observation_id,
        resolved_fee_regime_id=resolved_fee_regime_id,
        fee_policy_id=fee_policy.policy_id,
        orderbook_source_id=observation.source_id,
        orderbook_source_hash=observation.source_hash,
        market_observed_at=market_observed_at,
        orderbook_observed_at=orderbook_observed_at,
        economics_observed_at=economics_observed_at,
        requested_quantity=requested_quantity,
        yes=yes_cost,
        no=no_cost,
        replay_input=replay_input,
    )
    economics = MarketEconomicsEvidence.create(**values)

    binding = AuthoritativeMarketEconomicsBinding(
        SCHEMA,
        economics.evidence_id,
        economics.market_ticker,
        economics.event_ticker,
        economics.market_rules_hash,
        economics.market_metadata_hash,
        market_observed_at,
        economics_observed_at,
        economics.orderbook_source_hash,
        economics.price_range_hash,
        snapshot_payload,
    )
    return economics, binding


def _side_cost(
    observation: BookObservation, side: OutcomeSide, quantity: Decimal, policy: FeePolicy
) -> TakerCost | None:
    asks = observation.book.yes_asks if side is OutcomeSide.YES else observation.book.no_asks
    if not walk_depth(asks, quantity).complete:
        return None
    return taker_cost(observation.book, side, quantity, policy)


@dataclass(frozen=True, slots=True)
class BindingValidation:
    classification: str
    reason: str | None = None
    rules_hash: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def validate_authoritative_economics_market_binding(
    payload: object,
    *,
    economics: MarketEconomicsEvidence,
    expected_market_ticker: str,
    expected_event_ticker: str,
) -> BindingValidation:
    """Independently re-validate a serialized :class:`AuthoritativeMarketEconomicsBinding`.

    PASS only if every one of the following holds, all independently re-derived rather than
    merely trusted:

    * the binding's own shape/schema is exact and it references *this exact*
      ``economics.evidence_id`` (a binding for a different economics evidence can never validate
      a different one);
    * the embedded ``expected_snapshot`` independently re-validates through
      :func:`services.market_universe.market_snapshot.validate_market_snapshot` (this rejects a
      tampered raw body, a tampered body hash, a tampered stamped rules hash, a wrong ticker, or
      a wrong event -- see module docstring);
    * the binding's own stamped ticker/event/rules-hash/metadata-hash/observed_at fields match
      that independent re-derivation exactly (a binding cannot claim different top-level values
      than the snapshot it embeds);
    * ``economics`` itself -- the live, in-process object, not merely the serialized binding --
      matches the re-derived market identity/rules-hash/metadata-hash/observed_at, and its
      ``orderbook_source_hash``/``price_range_hash`` match the binding's stamped values;
    * the snapshot's acquisition time is at or before ``economics.economics_observed_at`` within
      the reviewed acquisition/evaluation skew bound.

    On success, returns the independently re-derived ``rules_hash`` -- callers (M27I) must use
    *that* value, never a bare ``economics.market_rules_hash`` string, as the expected-side
    comparison target for M27J's current-side evidence.
    """
    if not isinstance(payload, dict) or set(payload) != _BINDING_FIELDS:
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE", "binding payload has unexpected or missing fields"
        )
    if payload.get("schema") != SCHEMA:
        return BindingValidation("MALFORMED_BINDING_EVIDENCE", "binding schema mismatch")
    if payload.get("economics_evidence_id") != economics.evidence_id:
        return BindingValidation(
            "ECONOMICS_IDENTITY_MISMATCH",
            "binding does not reference the exact economics evidence in use",
        )

    snapshot_payload = payload.get("expected_snapshot")
    snapshot_result = validate_market_snapshot(
        snapshot_payload,
        expected_ticker=expected_market_ticker,
        expected_event_ticker=expected_event_ticker,
    )
    if not snapshot_result.succeeded:
        return BindingValidation(
            "EXPECTED_SNAPSHOT_INVALID",
            f"embedded expected market snapshot is invalid: {snapshot_result.classification} "
            f"({snapshot_result.reason})",
        )
    market_observed_at = snapshot_result.observed_at
    if (
        market_observed_at is None
        or snapshot_result.rules_hash is None
        or snapshot_result.metadata_hash is None
        or snapshot_result.ticker is None
        or snapshot_result.event_ticker is None
    ):  # pragma: no cover - PASS always carries all of these
        return BindingValidation(
            "EXPECTED_SNAPSHOT_INVALID", "expected snapshot validation incomplete"
        )

    if (
        payload.get("market_ticker") != snapshot_result.ticker
        or payload.get("event_ticker") != snapshot_result.event_ticker
        or payload.get("market_rules_hash") != snapshot_result.rules_hash
        or payload.get("market_metadata_hash") != snapshot_result.metadata_hash
    ):
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE",
            "binding's stamped fields do not match an independent re-derivation of its embedded "
            "expected snapshot",
        )
    binding_observed_raw = payload.get("market_observed_at")
    if not isinstance(binding_observed_raw, str):
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE", "binding market_observed_at malformed"
        )
    try:
        binding_observed_at = datetime.fromisoformat(binding_observed_raw)
    except ValueError:
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE", "binding market_observed_at malformed"
        )
    if binding_observed_at.tzinfo is None:
        return BindingValidation("MALFORMED_BINDING_EVIDENCE", "binding market_observed_at naive")
    if _utc(binding_observed_at) != market_observed_at:
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE",
            "binding market_observed_at does not match its embedded snapshot",
        )

    if (
        economics.market_ticker != snapshot_result.ticker
        or economics.event_ticker != snapshot_result.event_ticker
    ):
        return BindingValidation(
            "MARKET_IDENTITY_MISMATCH", "economics market/event identity does not match the binding"
        )
    if (
        economics.market_rules_hash != snapshot_result.rules_hash
        or economics.market_metadata_hash != snapshot_result.metadata_hash
    ):
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE",
            "economics rules/metadata hash does not match the independently re-derived snapshot",
        )
    if _utc(economics.market_observed_at) != market_observed_at:
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE",
            "economics market_observed_at does not match the binding's expected snapshot",
        )
    if payload.get("orderbook_source_hash") != economics.orderbook_source_hash:
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE", "binding orderbook_source_hash does not match economics"
        )
    if payload.get("price_range_hash") != economics.price_range_hash:
        return BindingValidation(
            "MALFORMED_BINDING_EVIDENCE", "binding price_range_hash does not match economics"
        )

    economics_at = _utc(economics.economics_observed_at)
    if market_observed_at > economics_at:
        return BindingValidation(
            "EXPECTED_SNAPSHOT_INVALID",
            "expected snapshot was acquired after the economics evaluation it backs",
        )
    if economics_at - market_observed_at > MAX_ACQUISITION_SKEW:
        return BindingValidation(
            "EXPECTED_SNAPSHOT_STALE",
            "expected snapshot acquisition/evaluation skew exceeds the reviewed bound",
        )

    return BindingValidation("PASS", None, rules_hash=snapshot_result.rules_hash)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpportunityError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
