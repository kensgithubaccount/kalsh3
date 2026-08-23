"""M27R operator-only one-command live weather candidate scan.

This script composes the already-reviewed public weather, public Kalshi, frozen
weather-model, authoritative market/orderbook, fee, and M27D candidate-selection
boundaries into one operator command.

Safety boundary:
- PUBLIC GETs only (NOAA + unauthenticated Kalshi production reads).
- no credential access, signer, account read, store mutation, M13 authorization,
  M16 approval, M27O execution authorization, arm, burn, or order capability;
- fail-closed outside the reviewed 03Z freshness window;
- create-only evidence publication into a unique private run directory;
- a qualifying candidate is only a handoff signal for the separately reviewed
  authenticated preflight boundary. It is never an order authorization.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.collect_m27c_weather_calibration_coverage import _get
from scripts.m27e_public_read_acceptance import (
    ACTIVE_MARKET_STATUS,
    SERIES_TICKER,
    paged_markets,
)
from scripts.select_parse_verified_current_weather_source import (
    select_parse_verified_current_source,
)
from services.forecasting.daily_temperature import route_daily_temperature
from services.forecasting.weather_probability import (
    WeatherProbabilityAbstention,
    build_current_weather_forecast_evidence,
    load_weather_residual_population,
    physical_temperature_proxy_probability,
)
from services.forecasting.weather_prospective import (
    FROZEN_MODEL_IDENTITIES,
    FROZEN_MODEL_TRAINING_END,
    FROZEN_MODEL_TRAINING_START,
)
from services.market_universe.event_snapshot import acquire_event_snapshot
from services.market_universe.market_snapshot import acquire_market_snapshot
from services.market_universe.orderbook_snapshot import acquire_orderbook_snapshot
from services.market_universe.pricing import PriceLadder
from services.market_universe.public_read import BASE, PublicReadFailure, get
from services.supervised_canary.m27d import CandidateState, select_experimental_candidate
from services.supervised_canary.m27n2_candidate_packet import reconstruct_economics
from services.supervised_canary.m27n2_evidence_reconstruction import (
    EvidenceReconstructionError,
    reconstruct_event,
    reconstruct_market,
)

SCHEMA = "kalsh3.m27r.weather-candidate-scan.v1"
SOFTWARE_VERSION = "kalsh3.scripts.run_m27r_weather_candidate_scan/1"
HORIZON_SECONDS = 54_000
WINDOW_START_MINUTE = 0
WINDOW_END_MINUTE = 25


class M27RScanError(RuntimeError):
    """The read-only M27R scan could not complete safely."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27RScanError("clock must be timezone-aware")
    return value.astimezone(UTC)


def _window_open(now: datetime) -> bool:
    current = _utc(now)
    return current.hour == 3 and WINDOW_START_MINUTE <= current.minute <= WINDOW_END_MINUTE


def _active_market_rows(markets_evidence: dict[str, object]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pages = markets_evidence.get("pages")
    if not isinstance(pages, list):
        raise M27RScanError("public market evidence pages are missing")
    for page in pages:
        if not isinstance(page, dict):
            raise M27RScanError("public market evidence page is malformed")
        payload = page.get("payload")
        if not isinstance(payload, dict):
            raise M27RScanError("public market evidence payload is malformed")
        markets = payload.get("markets")
        if not isinstance(markets, list):
            raise M27RScanError("public market evidence markets array is missing")
        for market in markets:
            if not isinstance(market, dict):
                raise M27RScanError("public market row is malformed")
            if market.get("status") != ACTIVE_MARKET_STATUS:
                continue
            ticker = market.get("ticker")
            event_ticker = market.get("event_ticker")
            if not isinstance(ticker, str) or not ticker.startswith(SERIES_TICKER + "-"):
                raise M27RScanError("active market ticker is outside the fixed series")
            if not isinstance(event_ticker, str) or not event_ticker:
                raise M27RScanError("active market event ticker is missing")
            rows.append(market)
    rows.sort(key=lambda item: str(item["ticker"]))
    return rows


def _create_run_dir(root: Path, now: datetime) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / _utc(now).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(mode=0o700, exist_ok=False)
    os.chmod(run_dir, 0o700)
    return run_dir


def _write_json_create_only(path: Path, payload: object) -> None:
    rendered = json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n"
    with path.open("x", encoding="utf-8") as output:
        output.write(rendered)
    os.chmod(path, 0o600)


def _series_raw(evidence: dict[str, object]) -> dict[str, Any]:
    if evidence.get("classification") != "SUCCESS":
        raise M27RScanError("current series GET did not succeed")
    payload = evidence.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("series"), dict):
        raise M27RScanError("current series payload is malformed")
    return payload["series"]


def _side_summary(cost: Any, model_probability: Decimal) -> dict[str, str | None]:
    if cost is None:
        return {
            "taker_price": None,
            "break_even_with_fee": None,
            "discrepancy": None,
        }
    break_even = cost.depth.total_cost + cost.centicent_rounded_fee
    return {
        "taker_price": str(cost.depth.worst_price),
        "break_even_with_fee": str(break_even),
        "discrepancy": str(model_probability - break_even),
    }


def run_scan(
    *,
    population_artifact: Path,
    wgrib2_bin: str,
    output_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    started_at = _utc(now or datetime.now(UTC))
    if not _window_open(started_at):
        raise M27RScanError("outside guarded 03:00Z-03:25Z acquisition window")

    run_dir = _create_run_dir(output_root, started_at)

    population_payload = json.loads(population_artifact.read_text())
    if not isinstance(population_payload, dict):
        raise M27RScanError("population artifact is not an object")
    population = load_weather_residual_population(
        population_payload,
        exact_midpoint_seconds=HORIZON_SECONDS,
        training_start=FROZEN_MODEL_TRAINING_START,
        training_end=FROZEN_MODEL_TRAINING_END,
    )
    if isinstance(population, WeatherProbabilityAbstention):
        raise M27RScanError(
            f"frozen population abstained: {population.reason.value} ({population.detail})"
        )
    expected_model = FROZEN_MODEL_IDENTITIES[HORIZON_SECONDS]
    if population.identity.model_id != expected_model:
        raise M27RScanError("frozen population model identity mismatch")

    selection = select_parse_verified_current_source(
        started_at.date(), transport=lambda url: _get(url, cache=None), wgrib2_bin=wgrib2_bin
    )
    if not selection.succeeded or selection.selected is None:
        raise M27RScanError(
            "current weather source selection failed: "
            f"{selection.classification} ({selection.reason})"
        )
    selected_weather = selection.selected
    current = build_current_weather_forecast_evidence(
        selected_weather.evidence, record_number=1
    )

    public_markets = paged_markets()
    if (
        public_markets.get("classification") != "SUCCESS"
        or public_markets.get("pagination_complete") is not True
    ):
        raise M27RScanError("public market pagination did not complete")
    active_rows = _active_market_rows(public_markets)

    exchange_evidence = get(BASE + "/exchange/status")
    if exchange_evidence.get("classification") != "SUCCESS":
        raise M27RScanError("exchange-status GET did not succeed")
    series_evidence = get(BASE + "/series/" + SERIES_TICKER)
    series_raw = _series_raw(series_evidence)

    _write_json_create_only(run_dir / "exchange-status.json", exchange_evidence)
    _write_json_create_only(run_dir / "series.json", series_evidence)
    _write_json_create_only(run_dir / "market-discovery.json", public_markets)

    event_cache: dict[str, tuple[dict[str, Any], Any]] = {}
    candidate_inputs = []
    market_rows: list[dict[str, Any]] = []

    for discovery in active_rows:
        ticker = str(discovery["ticker"])
        event_ticker = str(discovery["event_ticker"])

        if event_ticker not in event_cache:
            event_snapshot = acquire_event_snapshot(event_ticker)
            if not event_snapshot.succeeded:
                raise M27RScanError(
                    f"event snapshot failed for {event_ticker}: "
                    f"{event_snapshot.classification} ({event_snapshot.reason})"
                )
            event_payload = event_snapshot.to_json()
            event = reconstruct_event(event_payload, expected_ticker=event_ticker)
            if event.series_ticker != SERIES_TICKER:
                raise M27RScanError("event series ticker does not match the fixed series")
            event_cache[event_ticker] = (event_payload, event)
            _write_json_create_only(run_dir / f"event-{event_ticker}.json", event_payload)

        event_payload, event = event_cache[event_ticker]
        del event_payload

        market_snapshot = acquire_market_snapshot(ticker)
        orderbook_snapshot = acquire_orderbook_snapshot(ticker)
        if not market_snapshot.succeeded:
            raise M27RScanError(
                f"market snapshot failed for {ticker}: "
                f"{market_snapshot.classification} ({market_snapshot.reason})"
            )
        if not orderbook_snapshot.succeeded:
            raise M27RScanError(
                f"orderbook snapshot failed for {ticker}: "
                f"{orderbook_snapshot.classification} ({orderbook_snapshot.reason})"
            )

        market_payload = market_snapshot.to_json()
        orderbook_payload = orderbook_snapshot.to_json()
        _write_json_create_only(run_dir / f"market-{ticker}.json", market_payload)
        _write_json_create_only(run_dir / f"orderbook-{ticker}.json", orderbook_payload)

        market = reconstruct_market(
            market_payload,
            expected_ticker=ticker,
            expected_event_ticker=event_ticker,
        )
        route = route_daily_temperature(market, event)
        probability = physical_temperature_proxy_probability(
            route=route,
            population=population,
            current=current,
        )
        if isinstance(probability, WeatherProbabilityAbstention):
            market_rows.append(
                {
                    "ticker": ticker,
                    "event_ticker": event_ticker,
                    "state": "PROBABILITY_ABSTAIN",
                    "reason": f"{probability.reason.value}: {probability.detail}",
                }
            )
            continue

        ladder = PriceLadder.parse(
            market.raw.get("price_level_structure"), market.raw.get("price_ranges")
        )
        economics_at = datetime.now(UTC)
        economics, binding = reconstruct_economics(
            market_snapshot_payload=market_payload,
            orderbook_snapshot_payload=orderbook_payload,
            expected_market_ticker=ticker,
            expected_event_ticker=event_ticker,
            series_ticker=SERIES_TICKER,
            market_source_id=f"m27r-market:{market_snapshot.body_sha256}",
            ladder=ladder,
            orderbook_source_id=f"m27r-book:{orderbook_snapshot.body_sha256}",
            series_fee_payload=series_raw,
            event_fee_payload=event.raw,
            requested_quantity=Decimal("1.00"),
            economics_observed_at=economics_at,
            now=economics_at,
        )
        candidate_inputs.append((probability, current, economics))
        market_rows.append(
            {
                "ticker": ticker,
                "event_ticker": event_ticker,
                "state": "EVALUATED",
                "model_yes_probability": str(probability.probability),
                "model_no_probability": str(Decimal(1) - probability.probability),
                "yes": _side_summary(economics.yes, probability.probability),
                "no": _side_summary(economics.no, Decimal(1) - probability.probability),
                "weather_result_identity": probability.result_identity,
                "forecast_evidence_identity": current.evidence_identity,
                "economics_evidence_identity": economics.evidence_id,
                "rules_hash": binding.market_rules_hash,
                "orderbook_observed_at": economics.orderbook_observed_at.isoformat(),
            }
        )

    decision_now = datetime.now(UTC)
    result = select_experimental_candidate(tuple(candidate_inputs), now=decision_now)

    selected: dict[str, Any] | None = None
    next_gate = "NONE_ABSTAIN"
    if result.state is CandidateState.QUALIFYING_EXPERIMENTAL_CANARY:
        if result.selected is None:
            raise M27RScanError("qualifying candidate state did not carry a selected candidate")
        candidate = result.selected
        selected = {
            "candidate_id": candidate.candidate_id,
            "market_ticker": candidate.market_ticker,
            "event_ticker": candidate.event_ticker,
            "series_ticker": candidate.series_ticker,
            "selected_side": candidate.selected_side.value,
            "executable_price": str(candidate.executable_price),
            "maximum_fee": str(candidate.maximum_fee),
            "maximum_commitment": str(candidate.maximum_commitment),
            "maximum_loss": str(candidate.maximum_loss),
            "research_probability_discrepancy": str(
                candidate.research_probability_discrepancy
            ),
            "forecast_evidence_identity": candidate.eligibility.forecast_evidence_identity,
            "economics_evidence_identity": candidate.economics_evidence_identity,
        }
        next_gate = "AUTHENTICATED_M27Q_PREFLIGHT_REQUIRED"

    completed_at = datetime.now(UTC)
    summary: dict[str, Any] = {
        "schema": SCHEMA,
        "software_version": SOFTWARE_VERSION,
        "classification": "SUCCESS",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "run_dir": str(run_dir),
        "series_ticker": SERIES_TICKER,
        "active_market_count": len(active_rows),
        "evaluated_market_count": len(candidate_inputs),
        "forecast": {
            "selected_name": selected_weather.name,
            "raw_grib_sha256": selected_weather.raw_sha256,
            "extraction_sha256": selected_weather.extraction_sha256,
            "forecast_reference_time": current.forecast_reference_time.isoformat(),
            "local_target_date": current.local_target_date.isoformat(),
            "central_deg_f": str(current.central_deg_f),
            "exact_midpoint_seconds": current.exact_midpoint_seconds,
            "forecast_evidence_identity": current.evidence_identity,
        },
        "model": {
            "model_id": population.identity.model_id,
            "sample_count": population.identity.sample_count,
            "training_start": FROZEN_MODEL_TRAINING_START.isoformat(),
            "training_end": FROZEN_MODEL_TRAINING_END.isoformat(),
        },
        "markets": market_rows,
        "candidate_state": result.state.value,
        "candidate_reason": result.reason,
        "qualifying_candidate_count": len(result.candidates),
        "selected": selected,
        "next_gate": next_gate,
        "safety": {
            "credential_access": "NONE",
            "authenticated_account_gets": "NONE",
            "production_state_mutation": "NONE",
            "m13_authorization": "NONE",
            "m13_risk_reservation": "NONE",
            "m16_approval": "NONE",
            "m27o_execution_authorization": "NONE",
            "burn": "NONE",
            "order_sent": "NO",
        },
    }
    _write_json_create_only(run_dir / "summary.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "M27R one-command public read-only Chicago weather candidate scan. "
            "Must run in the guarded 03:00Z-03:25Z window. Never accesses credentials or trades."
        )
    )
    parser.add_argument("--population-artifact", required=True, type=Path)
    parser.add_argument("--wgrib2-bin", required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path.home() / ".kalsh3" / "evidence" / "m27r-weather-scan",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = run_scan(
            population_artifact=args.population_artifact,
            wgrib2_bin=args.wgrib2_bin,
            output_root=args.output_root,
        )
    except (
        M27RScanError,
        PublicReadFailure,
        EvidenceReconstructionError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"M27R_SCAN=BLOCKED\nREASON={type(exc).__name__}: {exc}")
        print("CREDENTIAL_ACCESS=NONE")
        print("PRODUCTION_STATE_MUTATION=NONE")
        print("ORDER_SENT=NO")
        return 2

    print("M27R_SCAN=PASS")
    print("RUN_DIR=" + summary["run_dir"])
    print("ACTIVE_MARKET_COUNT=" + str(summary["active_market_count"]))
    print("EVALUATED_MARKET_COUNT=" + str(summary["evaluated_market_count"]))
    print("CANDIDATE_STATE=" + str(summary["candidate_state"]))
    print("QUALIFYING_CANDIDATE_COUNT=" + str(summary["qualifying_candidate_count"]))
    selected = summary.get("selected")
    if isinstance(selected, dict):
        print("SELECTED_MARKET=" + str(selected.get("market_ticker")))
        print("SELECTED_SIDE=" + str(selected.get("selected_side")))
        print("EXECUTABLE_PRICE=" + str(selected.get("executable_price")))
        print(
            "RESEARCH_DISCREPANCY="
            + str(selected.get("research_probability_discrepancy"))
        )
    print("NEXT_GATE=" + str(summary["next_gate"]))
    print("SUMMARY=" + str(Path(str(summary["run_dir"])) / "summary.json"))
    print("CREDENTIAL_ACCESS=NONE")
    print("AUTHENTICATED_ACCOUNT_GETS=NONE")
    print("PRODUCTION_STATE_MUTATION=NONE")
    print("M13_AUTHORIZATION=NONE")
    print("M16_APPROVAL=NONE")
    print("M27O_EXECUTION_AUTHORIZATION=NONE")
    print("BURN=NONE")
    print("ORDER_SENT=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
