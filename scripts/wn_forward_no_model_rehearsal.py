"""Offline-only integrated WeatherNext source-NO-GO / five-city mock abstention rehearsal.

Connects the already-reviewed P2b source-geometry veto, P3 untrusted
six-sibling structural preflight, and P4 mock reservation/abstention
transitions. Every market row and clock is INVENTED by this rehearsal or
passed as a simulated clock. No I/O, source fetch, GCS, EE, Kalshi GET,
fees, depth, probability, storage, scheduling, paper, or trading.
A successful rehearsal is not a prospective observation or ledger receipt.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Sequence

from scripts.wn_p2b_offline_member_access_preflight import (
    NO_GO,
    OfflineMemberAccessNoGo,
    plan_global_member_access_no_go,
)
from services.forecasting.wn_p3_sibling_preflight import (
    CITY_SERIES,
    StructuralRoster,
    event_ticker_for,
    validate_untrusted_roster,
)
from services.forecasting.wn_p4_mock_attempt_protocol import (
    AttemptState,
    MockLedger,
    classify_mock_abstention,
    reserve_mock_date,
)

POLICY = "wn-forward-p4b-integrated-offline-all-abstention-rehearsal-v1"
STATUS = "MOCK_ALL_ABSTAIN_NO_MODEL_NOT_EVIDENCE"

# These six labels and all their numerical bounds are deliberately invented;
# they cannot be mistaken for acquired ticker/rule/executable-depth evidence.
_FAKE_BINS: tuple[tuple[str, str, int | None, int | None], ...] = (
    ("MOCKLT", "less", None, 70),
    ("MOCKB70", "between", 70, 71),
    ("MOCKB72", "between", 72, 73),
    ("MOCKB74", "between", 74, 75),
    ("MOCKB76", "between", 76, 77),
    ("MOCKGT", "greater", 77, None),
)


def _invented_roster(series: str, target: date) -> StructuralRoster:
    ticker = event_ticker_for(series, target)
    markets = [
        {
            "ticker": f"{ticker}-{suffix}",
            "event_ticker": ticker,
            "status": "active",
            "market_type": "binary",
            "rules_primary": "INVENTED_REHEARSAL_ONLY_NOT_EXCHANGE_RULES",
            "strike_type": strike_type,
            "floor_strike": floor,
            "cap_strike": cap,
        }
        for suffix, strike_type, floor, cap in _FAKE_BINS
    ]
    return validate_untrusted_roster(
        series_ticker=series,
        target_local_date=target,
        event_payload={
            "event": {"event_ticker": ticker, "series_ticker": series},
            "markets": markets,
        },
    )


@dataclass(frozen=True, slots=True)
class NoModelRehearsal:
    """Only a synthetic, non-durable report: no positive authority by construction."""

    source_geometry: OfflineMemberAccessNoGo
    mock_ledger: MockLedger

    @property
    def source_access_demonstrated(self) -> bool:
        return False

    @property
    def market_source_attested(self) -> bool:
        return False

    @property
    def probability_authorized(self) -> bool:
        return False

    @property
    def paper_authorized(self) -> bool:
        return False

    @property
    def trading_authority(self) -> str:
        return "NONE"

    @property
    def research_only(self) -> bool:
        return True

    def receipt(self) -> dict[str, object]:
        if len(self.mock_ledger.clusters) != 1:
            raise ValueError("rehearsal requires exactly one synthetic date cluster")
        cluster = self.mock_ledger.clusters[0]
        if (
            cluster.denominator_city_attempts != len(CITY_SERIES)
            or cluster.denominator_sibling_slots != 30
            or any(item.state is not AttemptState.NO_MODEL for item in cluster.attempts)
            or self.source_geometry.decision != NO_GO
            or self.source_geometry.target_local_date != cluster.target_local_date
        ):
            raise ValueError("rehearsal cannot issue inconsistent or positive state")

        return {
            "record_type": "WN-P4B-OFFLINE-NO-MODEL-REHEARSAL-v1",
            "status": STATUS,
            "policy": POLICY,
            "target_local_date": cluster.target_local_date.isoformat(),
            "simulated_only": True,
            "real_observation_count": 0,
            "gcs_ee_kalshi_requests_made": 0,
            "source_decision": NO_GO,
            "geometry_global_chunks": len(self.source_geometry.required_global_member_lead_keys),
            "geometry_global_decoded_bytes_not_network": (
                self.source_geometry.total_global_decoded_bytes
            ),
            "inherited_reader_network_cap_bytes": (
                self.source_geometry.inherited_reader_network_cap_bytes
            ),
            "observed_network_bytes": None,
            "observed_source_generation": None,
            "denominator_date_clusters": self.mock_ledger.denominator_date_clusters,
            "denominator_city_attempts": self.mock_ledger.denominator_city_attempts,
            "denominator_sibling_slots": self.mock_ledger.denominator_sibling_slots,
            "city_attempts": [
                {
                    "series_ticker": attempt.series_ticker,
                    "event_ticker": attempt.event_ticker,
                    "invented_sibling_slots": len(attempt.untrusted_sibling_tickers),
                    "simulated_reservation_utc": attempt.reserved_at_utc.isoformat(),
                    "simulated_classification_utc": (
                        None
                        if attempt.classified_at_utc is None
                        else attempt.classified_at_utc.isoformat()
                    ),
                    "state": attempt.state.value,
                }
                for attempt in cluster.attempts
            ],
            "real_source_attested": False,
            "real_market_economics_issued": False,
            "source_probability_issued": False,
            "forecast_skill_scored": False,
            "paper_authorized": False,
            "trading_authority": "NONE",
            "research_only": True,
            "production_influence": "0",
        }


def rehearse_all_no_model(
    target_local_date: date,
    *,
    simulated_reserved_at: datetime,
    simulated_classified_at: datetime,
) -> NoModelRehearsal:
    """Exercise inherited offline-only veto and complete mock negative transitions."""
    source = plan_global_member_access_no_go(target_local_date)
    untrusted_rosters = {
        series: _invented_roster(series, target_local_date) for series in sorted(CITY_SERIES)
    }
    mock = reserve_mock_date(
        MockLedger(),
        target_local_date=target_local_date,
        reserved_at=simulated_reserved_at,
        untrusted_rosters=untrusted_rosters,
    )
    for series in sorted(CITY_SERIES):
        mock = classify_mock_abstention(
            mock,
            target_local_date=target_local_date,
            series_ticker=series,
            reason=AttemptState.NO_MODEL,
            classified_at=simulated_classified_at,
        )
    result = NoModelRehearsal(source_geometry=source, mock_ledger=mock)
    result.receipt()
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Print only a simulated local JSON receipt; never accept or load source files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-local-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--simulated-reserved-at", required=True, help="ISO 8601 with timezone")
    parser.add_argument("--simulated-classified-at", required=True, help="ISO 8601 with timezone")
    args = parser.parse_args(argv)
    try:
        target = date.fromisoformat(args.target_local_date)
        reserved = datetime.fromisoformat(args.simulated_reserved_at.replace("Z", "+00:00"))
        classified = datetime.fromisoformat(
            args.simulated_classified_at.replace("Z", "+00:00")
        )
        result = rehearse_all_no_model(
            target,
            simulated_reserved_at=reserved,
            simulated_classified_at=classified,
        )
    except (ValueError, TypeError):
        print(
            json.dumps(
                {
                    "record_type": "WN-P4B-OFFLINE-NO-MODEL-REHEARSAL-FAILURE-v1",
                    "status": "MOCK_REHEARSAL_UNVERIFIED",
                    "simulated_only": True,
                    "paper_authorized": False,
                    "trading_authority": "NONE",
                    "production_influence": "0",
                },
                sort_keys=True,
            )
        )
        return 4
    print(json.dumps(result.receipt(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
