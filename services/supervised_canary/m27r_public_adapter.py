"""Public/current evidence adapter for the M27R first-canary runner.

The adapter scans the exact M27E KXHIGHCHI public-read scope, then reconstructs one independently
bound evidence slice per supported active market using existing reviewed components only:

* M27E fixed-origin unauthenticated GET discovery;
* exact Event/Market/Orderbook snapshot acquisition;
* canonical Event/Market reconstruction and daily-temperature routing;
* frozen current 03Z GRIB probability construction;
* authoritative M27A economics + binding;
* a separate, later M27J exact-market GET for current rules identity.

It never imports ``scripts`` and owns no credential, signer, account, risk-authorization, approval,
burn, sender, or exchange-mutation capability. Current GRIB evidence is supplied by the operator
layer because acquiring/locally replaying NOAA GRIB uses the already-reviewed scripts/subprocess
boundary; keeping that boundary out of ``services`` is intentional.

The adapter consumes a clock callable, not a frozen timestamp. Each reviewed acquisition helper
therefore records its actual acquisition time in live use, while tests may inject a fixed clock.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.forecasting.daily_temperature import (
    DailyTemperatureRouteState,
    route_daily_temperature,
)
from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import (
    RawGribEvidence,
    target_local_date,
    validate_raw_grib_max_t_evidence,
)
from services.forecasting.weather_probability import (
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
    WeatherProbabilityAbstention,
    build_current_weather_forecast_evidence,
    load_weather_residual_population,
    physical_temperature_proxy_probability,
)
from services.market_universe.event_snapshot import (
    AuthoritativeEventSnapshot,
    acquire_event_snapshot,
)
from services.market_universe.m27e_public_acceptance import (
    acquire_public_acceptance,
    active_market_payloads,
)
from services.market_universe.market_snapshot import (
    AuthoritativeMarketSnapshot,
    acquire_market_snapshot,
)
from services.market_universe.orderbook_snapshot import (
    AuthoritativeOrderbookSnapshot,
    acquire_orderbook_snapshot,
)
from services.market_universe.pricing import PriceLadder
from services.market_universe.public_read import PublicReadFailure
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
)

from . import m27j
from .m27n2_candidate_packet import reconstruct_economics
from .m27n2_evidence_reconstruction import (
    EvidenceReconstructionError,
    reconstruct_event,
    reconstruct_market,
)
from .m27r_operator_runner import Clock, M27RMarketEvidence, M27RPublicEvidence

SOFTWARE_VERSION = "kalsh3.m27r.public-evidence-adapter/2"

PublicAcceptanceAcquirer = Callable[..., dict[str, object]]
MarketSnapshotAcquirer = Callable[..., AuthoritativeMarketSnapshot]
EventSnapshotAcquirer = Callable[..., AuthoritativeEventSnapshot]
OrderbookSnapshotAcquirer = Callable[..., AuthoritativeOrderbookSnapshot]
RulesAcquirer = Callable[..., AuthoritativeMarketSnapshot]


class M27RPublicAdapterError(RuntimeError):
    """Public evidence could not be assembled without weakening a reviewed gate."""


def _clock_now(clock: Clock, *, field: str) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27RPublicAdapterError(f"{field} must be timezone-aware")
    return value


def _persist(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), sort_keys=True, indent=2) + "\n")


def _series_payload(public: Mapping[str, object]) -> dict[str, Any]:
    series = public.get("series")
    if not isinstance(series, dict) or series.get("classification") != "SUCCESS":
        raise M27RPublicAdapterError("M27E series fee evidence is unavailable")
    payload = series.get("payload")
    raw = payload.get("series") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raise M27RPublicAdapterError("M27E series payload is malformed")
    return raw


def _record_number_for_route(*, raw_grib: RawGribEvidence, local_date: date, timezone: str) -> int:
    matches = tuple(
        record.record_number
        for record in raw_grib.records
        if target_local_date(record, timezone) == local_date
    )
    if len(matches) != 1:
        raise M27RPublicAdapterError(
            "contract target date "
            f"{local_date.isoformat()} does not bind to exactly one GRIB record"
        )
    return matches[0]


def _price_ladder(raw_market: Mapping[str, Any]) -> PriceLadder:
    try:
        return PriceLadder.parse(
            raw_market.get("price_level_structure"),
            raw_market.get("price_ranges"),
        )
    except Exception as exc:
        raise M27RPublicAdapterError(f"market price ladder is unsupported: {exc}") from exc


@dataclass(frozen=True, slots=True)
class GetOnlyPublicEvidenceProvider:
    """Concrete M27R public provider for the current Chicago MaxT first-canary lane."""

    raw_grib_evidence: RawGribEvidence
    population_artifact_payload: Mapping[str, Any]
    population_training_start: date
    population_training_end: date
    requested_quantity: Decimal
    output_dir: Path
    public_acceptance_acquirer: PublicAcceptanceAcquirer = acquire_public_acceptance
    market_snapshot_acquirer: MarketSnapshotAcquirer = acquire_market_snapshot
    event_snapshot_acquirer: EventSnapshotAcquirer = acquire_event_snapshot
    orderbook_snapshot_acquirer: OrderbookSnapshotAcquirer = acquire_orderbook_snapshot
    rules_acquirer: RulesAcquirer = m27j.acquire_current_market_rules

    def collect_public_evidence(self, *, clock: Clock) -> M27RPublicEvidence:
        _clock_now(clock, field="public evidence start clock")
        if self.requested_quantity != Decimal(1):
            raise M27RPublicAdapterError("M27R first canary requires exactly one contract")
        try:
            validate_raw_grib_max_t_evidence(self.raw_grib_evidence)
            public = self.public_acceptance_acquirer(clock=clock)
            active = active_market_payloads(public)
        except (ForecastError, PublicReadFailure) as exc:
            raise M27RPublicAdapterError(f"public discovery failed: {exc}") from exc

        public_path = self.output_dir / "m27e-public-read.json"
        _persist(public, public_path)
        series_raw = _series_payload(public)

        markets: list[M27RMarketEvidence] = []
        for discovery_market in active:
            market_ticker = discovery_market.get("ticker")
            event_ticker = discovery_market.get("event_ticker")
            if not isinstance(market_ticker, str) or not isinstance(event_ticker, str):
                raise M27RPublicAdapterError("active M27E market identity is malformed")
            try:
                built = self._build_market_slice(
                    clock=clock,
                    market_ticker=market_ticker,
                    event_ticker=event_ticker,
                    series_raw=series_raw,
                )
            except (
                EvidenceReconstructionError,
                ForecastError,
                OpportunityError,
                PublicReadFailure,
                M27RPublicAdapterError,
            ):
                # Unsupported/insufficient individual markets are simply not candidates. The
                # complete M27E artifact remains available to M27I, so this does not fabricate a
                # successful current-market claim for a market whose exact evidence failed.
                continue
            if built is not None:
                markets.append(built)

        return M27RPublicEvidence(
            public_evidence_path=public_path,
            markets=tuple(sorted(markets, key=lambda item: item.market_ticker)),
        )

    def _build_market_slice(
        self,
        *,
        clock: Clock,
        market_ticker: str,
        event_ticker: str,
        series_raw: Mapping[str, Any],
    ) -> M27RMarketEvidence | None:
        # Expected-side economics authority. Each helper receives the live clock callable so its
        # retained acquisition timestamp cannot be frozen at operator-run start.
        expected_market_snapshot = self.market_snapshot_acquirer(
            market_ticker,
            clock=clock,
        )
        event_snapshot = self.event_snapshot_acquirer(event_ticker, clock=clock)
        orderbook_snapshot = self.orderbook_snapshot_acquirer(
            market_ticker,
            clock=clock,
        )
        if not all(
            snapshot.succeeded
            for snapshot in (expected_market_snapshot, event_snapshot, orderbook_snapshot)
        ):
            return None
        if (
            expected_market_snapshot.body_sha256 is None
            or event_snapshot.body_sha256 is None
            or orderbook_snapshot.body_sha256 is None
        ):
            raise M27RPublicAdapterError("successful public snapshot is missing retained body hash")

        market = reconstruct_market(
            expected_market_snapshot.to_json(),
            expected_ticker=market_ticker,
            expected_event_ticker=event_ticker,
        )
        event = reconstruct_event(event_snapshot.to_json(), expected_ticker=event_ticker)
        route = route_daily_temperature(market, event)
        if route.state is not DailyTemperatureRouteState.SUPPORTED or route.contract is None:
            return None
        if route.series_ticker != "KXHIGHCHI" or event.series_ticker != "KXHIGHCHI":
            return None

        record_number = _record_number_for_route(
            raw_grib=self.raw_grib_evidence,
            local_date=route.contract.local_date,
            timezone=route.contract.timezone,
        )
        current: CurrentWeatherForecastEvidence = build_current_weather_forecast_evidence(
            self.raw_grib_evidence,
            record_number=record_number,
        )
        population = load_weather_residual_population(
            self.population_artifact_payload,
            exact_midpoint_seconds=current.exact_midpoint_seconds,
            training_start=self.population_training_start,
            training_end=self.population_training_end,
        )
        if isinstance(population, WeatherProbabilityAbstention):
            return None
        probability = physical_temperature_proxy_probability(
            route=route,
            population=population,
            current=current,
        )
        if isinstance(probability, WeatherProbabilityAbstention):
            return None
        typed_probability: PhysicalTemperatureProxyProbability = probability

        economics_now = _clock_now(clock, field=f"economics clock for {market_ticker}")
        event_fee_payload = dict(event.raw)
        economics, binding = reconstruct_economics(
            market_snapshot_payload=expected_market_snapshot.to_json(),
            orderbook_snapshot_payload=orderbook_snapshot.to_json(),
            expected_market_ticker=market_ticker,
            expected_event_ticker=event_ticker,
            series_ticker=event.series_ticker,
            market_source_id=f"m27r-market:{expected_market_snapshot.body_sha256}",
            ladder=_price_ladder(market.raw),
            orderbook_source_id=f"m27r-orderbook:{orderbook_snapshot.body_sha256}",
            series_fee_payload=series_raw,
            event_fee_payload=event_fee_payload,
            requested_quantity=self.requested_quantity,
            economics_observed_at=economics_now,
            now=economics_now,
        )

        series_fee = CurrentSeriesFeeObservation.parse(dict(series_raw), observed_at=economics_now)
        event_fee = EventFeeOverride.parse(event_fee_payload)

        # Current-side rules authority is a deliberately separate exact-market acquisition after
        # economics construction. M27I compares this evidence against the independently validated
        # expected-side M27A binding; it is never a caller-supplied H == H shortcut.
        current_rules = self.rules_acquirer(market_ticker, clock=clock)
        if not current_rules.succeeded:
            return None
        if current_rules.body_sha256 is None:
            raise M27RPublicAdapterError("successful current-rules snapshot is missing body hash")

        safe_ticker = market_ticker.replace("/", "_")
        binding_path = self.output_dir / f"m27a-binding-{safe_ticker}.json"
        rules_path = self.output_dir / f"m27j-rules-{safe_ticker}.json"
        _persist(binding.to_json(), binding_path)
        _persist(current_rules.to_json(), rules_path)

        return M27RMarketEvidence(
            market_ticker=market_ticker,
            candidate_input=(typed_probability, current, economics),
            m27j_evidence_path=rules_path,
            m27a_binding_evidence_path=binding_path,
            current_series_fee_observation=series_fee,
            current_event_fee_override=event_fee,
            current_event_fee_observed_at=economics_now,
        )
