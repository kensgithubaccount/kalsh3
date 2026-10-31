"""Offline-only five-city forward negative-cycle integration.

The ONLY market input is an optional concrete, caller-supplied in-memory dict
of fabricated/untrusted Kalshi-style response envelopes. No socket, client,
provider credential, file acquisition, scheduler, source attestation, price,
fees, model signal, paper or trade path exists. Passing no envelopes performs
the complete all-NO_MODEL abstention workflow.

The local SQLite journal is persistent but owner-tamperable; clocks are
caller-selected and do NOT independently prove prospective observation.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .wn_forward_negative_ledger import (
    classify_negative,
    read_negative_date,
    reserve_date,
)
from .wn_forward_readonly_capture import CaptureError, capture_untrusted_six_sibling_bundle
from .wn_p3_sibling_preflight import CITY_SERIES

POLICY = "wn-forward-p4d-offline-negative-cycle-v1"


class OfflineCycleError(ValueError):
    """Invalid synthetic inputs or inconsistent completed negative record."""


def _validate_synthetic_input(
    envelopes: dict[str, dict[str, dict[str, object]]] | None,
) -> None:
    """Concrete dictionaries only: never accept a callable or live GET handle."""
    if envelopes is None:
        return
    if type(envelopes) is not dict or not set(envelopes).issubset(CITY_SERIES):
        raise OfflineCycleError("only five named synthetic city bundles may be supplied")
    for city, responses in envelopes.items():
        if type(city) is not str or type(responses) is not dict:
            raise OfflineCycleError("synthetic city evidence must be a concrete dictionary")
        if any(
            type(path) is not str or type(response) is not dict
            for path, response in responses.items()
        ):
            raise OfflineCycleError("synthetic response envelopes must be concrete dictionaries")


def run_offline_negative_cycle(
    journal_path: Path,
    target_local_date: date,
    *,
    reserved_at: datetime,
    classified_at: datetime,
    synthetic_envelopes: dict[str, dict[str, dict[str, object]]] | None = None,
) -> dict[str, object]:
    """Reserve all five cities first, then attempt one negative transition each.

    None: no market lookup at all; NO_MODEL for every city.
    Concrete synthetic dictionaries: exercise P3 strict replay without any
    public GET. Missing or invalid city bundle => MARKET_MISSING (simulation
    only). Even when a complete synthetic bundle passes structural replay,
    the city remains NO_MODEL because an executable distribution is absent.

    A crash or invalid terminal clock after reservation deliberately leaves
    RESERVED records visible. No rollback of the durable reservation, no
    second read, no retries, no auto-resume, no positive outcomes.
    """
    _validate_synthetic_input(synthetic_envelopes)
    # This is the *only* date reservation. It atomically creates all 5 rows.
    # Duplicate dates fail before looking at any simulated market responses.
    reserve_date(journal_path, target_local_date, stamped_at=reserved_at)

    valid_synthetic_bundles = 0
    invalid_or_missing_synthetic_bundles = 0
    for series in sorted(CITY_SERIES):
        reason = "NO_MODEL"
        if synthetic_envelopes is not None:
            city_responses = synthetic_envelopes.get(series)
            if city_responses is None:
                reason = "MARKET_MISSING"
                invalid_or_missing_synthetic_bundles += 1
            else:
                try:
                    # get is ALWAYS supplied. No code path calls Kalshi.
                    # The default network transport is never selected.
                    _ = capture_untrusted_six_sibling_bundle(
                        series=series,
                        target=target_local_date,
                        evaluated_at=classified_at,
                        get=city_responses.__getitem__,
                    )
                except (CaptureError, KeyError, ValueError, TypeError, OverflowError):
                    reason = "MARKET_MISSING"
                    invalid_or_missing_synthetic_bundles += 1
                else:
                    valid_synthetic_bundles += 1

        # A failed or interrupted write is NOT overwritten, resumed or
        # silently completed; read_negative_date exposes reserved attempts.
        classify_negative(
            journal_path,
            target_local_date,
            series,
            reason=reason,
            stamped_at=classified_at,
        )

    persisted = read_negative_date(journal_path, target_local_date)
    rows = persisted["rows"]
    if (
        not isinstance(rows, list)
        or len(rows) != len(CITY_SERIES)
        or tuple(item.get("series") for item in rows if isinstance(item, dict))
        != tuple(sorted(CITY_SERIES))
        or any(
            not isinstance(item, dict) or item.get("status") not in {"NO_MODEL", "MARKET_MISSING"}
            for item in rows
        )
        or persisted["denominator_city_attempts"] != 5
        or persisted["denominator_sibling_slots"] != 30
    ):
        raise OfflineCycleError("persisted negative cycle not complete; refuse success")

    return {
        "record_type": "WN-P4D-LOCAL-OFFLINE-NEGATIVE-CYCLE-v1",
        "policy": POLICY,
        "status": "OFFLINE_NEGATIVE_ONLY_NOT_SOURCE_EVIDENCE",
        "simulated_only": True,
        "target_local_date": target_local_date.isoformat(),
        "journal": persisted,
        "synthetic_bundle_validation_count": valid_synthetic_bundles,
        "synthetic_missing_or_invalid_bundle_count": invalid_or_missing_synthetic_bundles,
        "real_observation_count": 0,
        "live_kalshi_get_authorized": False,
        "real_source_attested": False,
        "external_immutable_custody": False,
        "independently_trusted_clock": False,
        "model_probability_authorized": False,
        "market_economics_issued": False,
        "paper_authorized": False,
        "trading_authority": "NONE",
        "production_influence": "0",
        "research_only": True,
    }
