"""Offline CPI-E1-P10A historical binding and market-baseline gate.

This module is deliberately a reader of the immutable P8/P9A artifacts.  It has
no network, account, execution, fee, or production influence capability.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from services.forecasting.cpi_annual_archive_acquisition import load_frozen_target_cohort
from services.historical_replay.cpi_price_evidence import validate_frozen_cohort

P9A_ROOT = Path("evidence/cpi_p9a_historical_price")
P7_VALUES: dict[tuple[int, int], Decimal] = {
    (2025, 7): Decimal("0.2"),
    (2025, 12): Decimal("0.3"),
    (2026, 1): Decimal("0.2"),
}
P7_OBSERVATION_IDS = {
    (2025, 7): "74b5c6f504d448ac475a5598e50a0602b249368acd26b90642c066ecd96f4c65",
    (2025, 12): "9cbc587c2fe7a8664e9a9546ad6a672e7914719cadc62a9cf03025affc4be0af",
    (2026, 1): "6b566274e63c5c6d65f11ab193c0275b30264cdeae428bb24a442dde0bfdbbda",
}
MONTHS = {
    name: i
    for i, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}
EVENT_RE = re.compile(r"^(?:CPI|KXCPI)-(\d{2})([A-Z]{3})$")
PREDICATE_RE = re.compile(
    r"more than\s+(-?\d+(?:\.\d+)?)%.*?\bin\s+([A-Za-z]+)\s+(\d{4})",
    re.I,
)
PREDICATE_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
P7_TIMING_ARTIFACT = Path("docs/reviews/artifacts/cpi-p7-release-timing.json")
P7_TIMING_SCHEMA = "cpi-e1-p7-release-timing-receipt-v1"
P5A_RECEIPT = Path("docs/reviews/CPI_E1_P5A_EMPIRICAL_SMOKE_RECEIPT.md")
P6_RECEIPT = Path("docs/reviews/CPI_E1_P6_INITIAL_RELEASE_VALUE_EVIDENCE.md")
P7_RECEIPT = Path("docs/reviews/CPI_E1_P7_SETTLEMENT_RECONCILIATION.md")
P5A_RECEIPT_SHA256 = "097434bb64de7750a7793255841db38a00d6e8be3c22aff2517f03989ecfc836"
P6_RECEIPT_SHA256 = "4eaf6492ea85a8042b4447a9c0abc66e6e3746af20c0f1f9c4be6ffaab2384b4"
P7_RECEIPT_SHA256 = "cec4c1bd323a7ce142db6ca53507a70afccc2ebbec917934d6bbc26861c36f20"
P7_TIMING_RAW_SHA256 = "d11687c9ba0f3abab5e62d8431041e7160d38925f602f1f522b638c60c2b8a20"
P7_TIMING_SEMANTIC_DIGEST = "bcf450b5a735b022ee5b62342979907d824f89bfea8e9440be4d64e6d9cb512a"
P7_TIMING_APPROVED: dict[str, tuple[str, str, str, str, str, str, str, str, str, str]] = {
    "2025-07": (
        "2025-08-12T08:30:00-04:00",
        "2025-08-12T12:30:00Z",
        "https://www.bls.gov/news.release/archives/cpi_08122025.htm",
        "5b869d4365bc0f58db9814e3da09105f0fd944e4bbf16c39b5511f774a03dc4b",
        "74b5c6f504d448ac475a5598e50a0602b249368acd26b90642c066ecd96f4c65",
        "5dc15e24b8196c2bb3718997a5cc00ff57ad777f7e4442c212f60d19c84017be",
        "fd8a4fdafe7ea67fd8197174f7fe463592ac81026ece3d2f888a67631dd25eb5",
        "3314c1d5b3ac34c83b62263edbc141548c8585e0c1b345bc2ce599f99fa74a66",
        "00da4834ba953288337a042a2d61133a19f4508fec95e24e4ccb93213bb410a1",
        "0.2",
    ),
    "2025-12": (
        "2026-01-13T08:30:00-05:00",
        "2026-01-13T13:30:00Z",
        "https://www.bls.gov/news.release/archives/cpi_01132026.htm",
        "8351af0db99f8b1e338abe1b33cb062a70e61d2b154c0ec26aaed964f52b489e",
        "9cbc587c2fe7a8664e9a9546ad6a672e7914719cadc62a9cf03025affc4be0af",
        "2f8f7300b553fdb6364aedd858d1672b92fdb05f1865916eee8904a325016846",
        "911db8be1386eacaebf1138a32351cbe8d4dfbcda942cbae8e09c5f8ad9dad19",
        "5bf66ffaa9067432317899fb6a90cde8013ca9ad09b97594b5bacda39e296ccb",
        "0d6032aa22b623af5b8178ee8e0b90d6d354f8ecffeef086037e2458acb97433e",
        "0.3",
    ),
    "2026-01": (
        "2026-02-13T08:30:00-05:00",
        "2026-02-13T13:30:00Z",
        "https://www.bls.gov/news.release/archives/cpi_02132026.htm",
        "3b46aebecd5aa2d66f6f8abc38e47381e180a73db6cf87313ecc8eeddebd69f8",
        "6b566274e63c5c6d65f11ab193c0275b30264cdeae428bb24a442dde0bfdbbda",
        "cd5684a7d61533b39fb05fbb1e6fbac024093438de4b00e824469b0ef51dc4f3",
        "058b88e52d330e12e00b07fa9278763a448fc185efbb2672c422ec60e184ed0e",
        "4df1cbd4637e37c2de453cb57239e2cce653b15fd9c4ef648b7f51a27d419721",
        "49cd36d6cc68f57b3e155e568339411a2f878df0b61516e9af57261d1a50a33e",
        "0.2",
    ),
}


class P10ABindingError(ValueError):
    """A required identity or immutable artifact check failed."""


@dataclass(frozen=True, slots=True)
class EventRow:
    event_ticker: str
    underlying_event_id: str
    reference_month: tuple[int, int]
    release_at: datetime
    cutoff_at: datetime
    market_ticker: str
    threshold: Decimal
    outcome: int
    probability: Decimal | None
    quote_age_seconds: int | None
    staleness: str
    p9a_evidence_id: str
    request_identity: str
    rejection: str | None


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise P10ABindingError("timestamp is malformed")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise P10ABindingError("timestamp is not timezone-aware")
    return result.astimezone(UTC)


def _strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise P10ABindingError("timing receipt contains duplicate JSON keys")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise P10ABindingError(f"timing receipt contains non-standard JSON constant: {value}")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise P10ABindingError("timing receipt is not an object")
    return value


def _load_p7_timing(root: Path) -> dict[tuple[int, int], tuple[str, datetime]]:
    raw = (root / P7_TIMING_ARTIFACT).read_bytes()
    if sha256(raw).hexdigest() != P7_TIMING_RAW_SHA256:
        raise P10ABindingError("P7 timing receipt raw SHA-256 mismatch")
    timing = _strict_json(raw)
    if set(timing) != {
        "schema_version",
        "authority",
        "events",
        "provenance_documents",
        "provenance",
        "research_only",
        "production_influence",
    }:
        raise P10ABindingError("P7 timing receipt schema fields are not exact")
    if (
        timing["schema_version"] != P7_TIMING_SCHEMA
        or timing["authority"] != "CPI_E1_P6_P7_REVIEW_RECEIPT"
    ):
        raise P10ABindingError("P7 timing receipt schema or authority is invalid")
    if timing["research_only"] is not True or timing["production_influence"] != "0":
        raise P10ABindingError("P7 timing receipt safety fields are invalid")
    documents = timing["provenance_documents"]
    expected_documents = {
        "p5a_publication_timing": (P5A_RECEIPT, P5A_RECEIPT_SHA256),
        "p6_initial_release_values": (P6_RECEIPT, P6_RECEIPT_SHA256),
        "p7_settlement_reconciliation": (P7_RECEIPT, P7_RECEIPT_SHA256),
    }
    if not isinstance(documents, dict) or set(documents) != set(expected_documents):
        raise P10ABindingError("P7 timing provenance document set is not exact")
    for name, (path, expected_hash) in expected_documents.items():
        item = documents[name]
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise P10ABindingError("P7 timing provenance document schema is invalid")
        if item["path"] != str(path) or item["sha256"] != expected_hash:
            raise P10ABindingError("P7 timing provenance document mapping is invalid")
        actual_hash = sha256((root / path).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise P10ABindingError(f"{name} receipt SHA-256 mismatch")
    events = timing["events"]
    if not isinstance(events, list) or len(events) != 3:
        raise P10ABindingError("P7 timing receipt must contain exactly three events")
    seen: set[str] = set()
    material: list[str] = [timing["schema_version"], timing["authority"]]
    result: dict[tuple[int, int], tuple[str, datetime]] = {}
    for item in events:
        if not isinstance(item, dict) or set(item) != {
            "reference_month",
            "publication_local",
            "publication_utc",
            "source_url",
            "artifact_sha256",
            "observation_id",
            "p5a_acquisition_evidence_id",
            "p5a_artifact_id",
            "p5a_timing_evidence_id",
            "p5a_publication_evidence_id",
            "initial_release_value",
        }:
            raise P10ABindingError("P7 timing event schema is invalid")
        reference = str(item["reference_month"])
        if reference in seen or reference not in P7_TIMING_APPROVED:
            raise P10ABindingError("P7 timing receipt has duplicate, missing, or extra event")
        seen.add(reference)
        approved = P7_TIMING_APPROVED[reference]
        actual = tuple(
            str(item[key])
            for key in (
                "publication_local",
                "publication_utc",
                "source_url",
                "artifact_sha256",
                "observation_id",
            )
        )
        p5a_actual = tuple(
            str(item[key])
            for key in (
                "p5a_acquisition_evidence_id",
                "p5a_artifact_id",
                "p5a_timing_evidence_id",
                "p5a_publication_evidence_id",
                "initial_release_value",
            )
        )
        if actual != approved[:5] or p5a_actual != approved[5:]:
            raise P10ABindingError("P7 timing receipt differs from approved semantic mapping")
        year, month = (int(part) for part in reference.split("-"))
        if Decimal(p5a_actual[4]) != P7_VALUES[(year, month)]:
            raise P10ABindingError("timing receipt value does not match P6/P7 authority")
        local = datetime.fromisoformat(actual[0])
        published = _time(actual[1])
        if local.astimezone(UTC) != published:
            raise P10ABindingError("P7 timing local and UTC instants disagree")
        material.extend((reference, *actual, *p5a_actual))
        result[(year, month)] = (actual[4], published)
    if seen != set(P7_TIMING_APPROVED):
        raise P10ABindingError("P7 timing receipt event set is incomplete")
    material.extend(
        (
            str(timing["provenance"]),
            str(timing["research_only"]),
            str(timing["production_influence"]),
            *(
                f"{name}:{documents[name]['path']}:{documents[name]['sha256']}"
                for name in sorted(documents)
            ),
        )
    )
    if sha256("|".join(material).encode()).hexdigest() != P7_TIMING_SEMANTIC_DIGEST:
        raise P10ABindingError("P7 timing receipt semantic digest mismatch")
    return result


def _event_month(event_ticker: str) -> tuple[int, int]:
    match = EVENT_RE.fullmatch(event_ticker)
    if match is None:
        raise P10ABindingError("event ticker grammar is invalid")
    return 2000 + int(match.group(1)), MONTHS[match.group(2)]


def _predicate(row: dict[str, Any]) -> tuple[Decimal, tuple[int, int]]:
    matches = list(PREDICATE_RE.finditer(str(row.get("rules_primary", ""))))
    if len(matches) != 1:
        raise P10ABindingError("contract predicate is absent or ambiguous")
    match = matches[0]
    month_name = match.group(2).lower()
    if month_name not in PREDICATE_MONTHS:
        raise P10ABindingError("contract predicate is absent or ambiguous")
    return Decimal(match.group(1)), (int(match.group(3)), PREDICATE_MONTHS[month_name])


def _logloss(probability: Decimal, outcome: int) -> float:
    clipped = min(max(float(probability), 0.01), 0.99)
    return -(math.log(clipped) if outcome else math.log1p(-clipped))


def _probability(row: EventRow) -> Decimal:
    if row.probability is None:
        raise P10ABindingError("internal non-executable quote reached scorer")
    return row.probability


def _calibration_bins(scored_rows: list[EventRow]) -> list[dict[str, Any]]:
    """Bin individual sibling diagnostics before event-equal aggregation."""
    calibration: list[dict[str, Any]] = []
    for lower in (0.0, 0.2, 0.4, 0.6, 0.8):
        selected_by_event: dict[str, list[EventRow]] = {}
        for row in scored_rows:
            probability_float = float(_probability(row))
            if lower <= probability_float < lower + 0.2:
                selected_by_event.setdefault(row.event_ticker, []).append(row)
        selected = [
            (
                sum(float(_probability(row)) for row in members) / len(members),
                sum(row.outcome for row in members) / len(members),
            )
            for members in selected_by_event.values()
        ]
        calibration.append(
            {
                "bin": f"[{lower:.1f},{lower + 0.2:.1f})",
                "events": len(selected),
                "sibling_rows": sum(len(members) for members in selected_by_event.values()),
                "mean_prediction": sum(value[0] for value in selected) / len(selected)
                if selected
                else None,
                "event_rate": sum(value[1] for value in selected) / len(selected)
                if selected
                else None,
            }
        )
    return calibration


def build_binding(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root)
    p9a_root = root / P9A_ROOT
    validate_frozen_cohort(p9a_root)
    manifest = json.loads((p9a_root / "manifest.json").read_bytes())
    inventory = json.loads((p9a_root / "market_inventory.json").read_bytes())["markets"]
    truth: dict[tuple[int, int], tuple[Decimal, str, datetime]] = {}
    for observation in load_frozen_target_cohort(root):
        truth[(observation.reference_year, observation.reference_month)] = (
            observation.value,
            observation.member_sha256,
            observation.release_instant.astimezone(UTC),
        )
    for month, (observation_id, published) in _load_p7_timing(root).items():
        if month not in P7_VALUES or observation_id != P7_OBSERVATION_IDS[month]:
            raise P10ABindingError("P7 timing receipt does not bind the exact observation")
        truth[month] = (P7_VALUES[month], observation_id, published)
    if set(P7_VALUES) - set(truth):
        raise P10ABindingError("P7 timing authority is incomplete")
    p9a_by_ticker = {row["market_ticker"]: row for row in manifest["markets"]}
    rows: list[EventRow] = []
    for market in inventory:
        ticker = market.get("ticker")
        event = market.get("event_ticker")
        rejection: str | None = None
        try:
            if not isinstance(ticker, str) or not isinstance(event, str):
                raise P10ABindingError("missing market/event identity")
            reference = _event_month(event)
            p9a = p9a_by_ticker.get(ticker)
            if p9a is None:
                raise P10ABindingError("missing P9A evidence identity")
            if p9a.get("underlying_event_id") != f"kalshi:{event}":
                raise P10ABindingError("underlying event identity mismatch")
            predicate, predicate_month = _predicate(market)
            if predicate_month != reference:
                raise P10ABindingError("predicate/reference month mismatch")
            if p9a.get("comparator") != "GT" or p9a.get("comparator_symbol") != ">":
                raise P10ABindingError("threshold/predicate mismatch")
            try:
                p9a_threshold = Decimal(str(p9a["threshold"]))
            except (KeyError, ValueError) as exc:
                raise P10ABindingError("P9A threshold is malformed or missing") from exc
            if p9a_threshold != predicate:
                raise P10ABindingError("threshold/predicate mismatch")
            if reference not in truth:
                raise P10ABindingError("missing authoritative initial-release truth")
            value, _truth_id, release_at = truth[reference]
            outcome = int(value > predicate)
            if (
                market.get("result") not in {"yes", "no"}
                or int(market["result"] == "yes") != outcome
            ):
                raise P10ABindingError("settlement outcome does not match bound predicate")
            probability = (
                None if p9a.get("yes_ask") in (None, "1.0000") else Decimal(str(p9a["yes_ask"]))
            )
            age = p9a.get("quote_age_seconds")
            cutoff = _time(p9a["market_close"])
            if p9a.get("candle_end_period_ts") is not None and p9a["candle_end_period_ts"] >= int(
                cutoff.timestamp()
            ):
                raise P10ABindingError("post-cutoff quote")
            if not (datetime.fromtimestamp(p9a["candle_end_period_ts"], UTC) < cutoff < release_at):
                raise P10ABindingError("quote/cutoff/release temporal order is invalid")
            rows.append(
                EventRow(
                    event,
                    f"kalshi:{event}",
                    reference,
                    release_at,
                    cutoff,
                    ticker,
                    predicate,
                    outcome,
                    probability,
                    age,
                    str(p9a.get("staleness_state")),
                    str(p9a["evidence_id"]),
                    str(p9a["request_identity"]),
                    None,
                )
            )
        except (KeyError, TypeError, ValueError, P10ABindingError) as exc:
            rejection = str(exc)
            if isinstance(event, str) and isinstance(ticker, str):
                rows.append(
                    EventRow(
                        event,
                        f"kalshi:{event}",
                        _event_month(event) if EVENT_RE.fullmatch(event) else (0, 0),
                        datetime.min.replace(tzinfo=UTC),
                        datetime.min.replace(tzinfo=UTC),
                        ticker,
                        Decimal(0),
                        0,
                        None,
                        None,
                        "REJECTED",
                        str(p9a_by_ticker.get(ticker, {}).get("evidence_id", "")),
                        str(p9a_by_ticker.get(ticker, {}).get("request_identity", "")),
                        rejection,
                    )
                )
    groups: dict[str, list[EventRow]] = {}
    for row in rows:
        if row.rejection is None:
            groups.setdefault(row.event_ticker, []).append(row)
    usable_events = {
        event: members
        for event, members in groups.items()
        if any(row.probability is not None for row in members)
    }
    event_scores: list[dict[str, Any]] = []
    for event, members in sorted(usable_events.items()):
        scored = [row for row in members if row.probability is not None]
        event_scores.append(
            {
                "event_ticker": event,
                "siblings": len(members),
                "scored_siblings": len(scored),
                "brier": sum((float(_probability(row)) - row.outcome) ** 2 for row in scored)
                / len(scored),
                "log_loss": sum(_logloss(_probability(row), row.outcome) for row in scored)
                / len(scored),
            }
        )
    bound = [row for row in rows if row.rejection is None]
    scored_rows = [row for row in bound if row.probability is not None]
    p9a_bound = [p9a_by_ticker[row.market_ticker] for row in bound]
    outcome_by_ticker = {row.market_ticker: row.outcome for row in bound}
    quote_counts = {
        "two_sided": sum(
            row.get("yes_bid") not in (None, "0.0000")
            and row.get("yes_ask") not in (None, "1.0000")
            for row in p9a_bound
        ),
        "one_sided": sum(
            (row.get("yes_bid") in (None, "0.0000")) != (row.get("yes_ask") in (None, "1.0000"))
            for row in p9a_bound
        ),
        "boundary": sum(
            row.get("yes_bid") in (None, "0.0000") or row.get("yes_ask") in (None, "1.0000")
            for row in p9a_bound
        ),
        "stale": sum(row.get("staleness_state") == "STALE" for row in p9a_bound),
        "unusable": len(bound) - len(scored_rows),
        "midpoint_eligible": sum(
            row.get("yes_bid") not in (None, "0.0000")
            and row.get("yes_ask") not in (None, "1.0000")
            for row in p9a_bound
        ),
        "exclusion_reasons": {
            str(reason): sum(row.get("missing_side_reason") == reason for row in p9a_bound)
            for reason in sorted(
                {
                    str(row.get("missing_side_reason"))
                    for row in p9a_bound
                    if row.get("missing_side_reason") is not None
                }
            )
        },
    }

    def quote_score(field: str) -> dict[str, float | int]:
        eligible = [
            row
            for row in p9a_bound
            if row.get("yes_bid") not in (None, "0.0000")
            and row.get("yes_ask") not in (None, "1.0000")
            and (field != "_midpoint" or "_midpoint" in row)
        ]
        by_event: dict[str, list[dict[str, Any]]] = {}
        for row in eligible:
            by_event.setdefault(str(row["event_ticker"]), []).append(row)
        event_brier: list[float] = []
        event_log: list[float] = []
        spreads: list[Decimal] = []
        for members in by_event.values():
            scores = [
                (Decimal(str(row[field])), outcome_by_ticker[str(row["market_ticker"])])
                for row in members
            ]
            event_brier.append(
                sum((float(price) - outcome) ** 2 for price, outcome in scores) / len(scores)
            )
            event_log.append(
                sum(_logloss(price, outcome) for price, outcome in scores) / len(scores)
            )
            spread = sum(
                (Decimal(str(row["yes_ask"])) - Decimal(str(row["yes_bid"]))) for row in members
            )
            spreads.append(spread / Decimal(len(members)))
        return {
            "events": len(by_event),
            "sibling_rows": len(eligible),
            "brier": sum(event_brier) / len(event_brier) if event_brier else float("nan"),
            "log_loss": sum(event_log) / len(event_log) if event_log else float("nan"),
            "mean_spread": float(sum(spreads) / len(spreads)) if spreads else float("nan"),
        }

    for row in p9a_bound:
        if row.get("yes_bid") not in (None, "0.0000") and row.get("yes_ask") not in (
            None,
            "1.0000",
        ):
            row["_midpoint"] = str(
                (Decimal(str(row["yes_bid"])) + Decimal(str(row["yes_ask"]))) / 2
            )
    interval_diagnostics = {
        "bid": quote_score("yes_bid"),
        "ask": quote_score("yes_ask"),
        "midpoint": quote_score("_midpoint"),
    }

    def subgroup(key: Callable[[EventRow], bool]) -> dict[str, float | int]:
        selected = [row for row in scored_rows if key(row)]
        by_event: dict[str, list[EventRow]] = {}
        for row in selected:
            by_event.setdefault(row.event_ticker, []).append(row)
        event_brier = [
            sum((float(_probability(row)) - row.outcome) ** 2 for row in members) / len(members)
            for members in by_event.values()
        ]
        event_log_loss = [
            sum(_logloss(_probability(row), row.outcome) for row in members) / len(members)
            for members in by_event.values()
        ]
        return {
            "rows": len(selected),
            "events": len(by_event),
            "brier": sum(event_brier) / len(event_brier) if event_brier else float("nan"),
            "log_loss": sum(event_log_loss) / len(event_log_loss)
            if event_log_loss
            else float("nan"),
        }

    calibration = _calibration_bins(scored_rows)
    return {
        "schema_version": "cpi-e1-p10a-binding-v1",
        "p9a_events": len(manifest["events"]),
        "p9a_siblings": len(manifest["markets"]),
        "bound_rows": len(bound),
        "ask_usable_rows": sum(row.probability is not None for row in bound),
        "quote_evidence_vs_executable": {
            "quote_evidence_rows": len(bound),
            "ask_crossing_quote_rows": len(scored_rows),
            "executable_definition": (
                "strictly pre-cutoff, valid non-boundary YES ask; crossing-price "
                "evidence only, with no depth or fill authority"
            ),
            "quote_counts": quote_counts,
            "ask_crossing": {
                "rows": len(scored_rows),
                "events": len(event_scores),
                "diagnostic": "YES ask crossing-price evidence, not a neutral probability estimate",
                "depth_or_fill_authority": False,
            },
            "two_sided_bid_ask": interval_diagnostics,
            "midpoint": {
                **interval_diagnostics["midpoint"],
                "executable": False,
                "diagnostic": "non-executable midpoint only when both valid sides exist",
            },
            "boundary_one_sided_missing": {
                "retained_in_denominator": True,
                "counts": quote_counts,
            },
        },
        "bound_events": len(groups),
        "usable_events": len(usable_events),
        "rejected_rows": [
            {"market_ticker": row.market_ticker, "reason": row.rejection}
            for row in rows
            if row.rejection
        ],
        "crossing_price_diagnostic": event_scores,
        "accepted_threshold_identity": [
            {
                "event_ticker": row.event_ticker,
                "market_ticker": row.market_ticker,
                "threshold": str(row.threshold),
                "comparator": "GT",
                "predicate_identity": f"GT:{row.threshold}",
                "p9a_evidence_id": row.p9a_evidence_id,
                "request_identity": row.request_identity,
            }
            for row in bound
        ],
        "crossing_price_brier_diagnostic": sum(item["brier"] for item in event_scores)
        / len(event_scores),
        "crossing_price_log_loss_diagnostic": sum(item["log_loss"] for item in event_scores)
        / len(event_scores),
        "log_loss_clipping": "probabilities clipped to [0.01, 0.99] before natural-log scoring",
        "crossing_price_calibration_clustered_by_event": calibration,
        "performance_by_time_to_cutoff": {
            "fresh_age_le_1h": subgroup(
                lambda row: row.quote_age_seconds is not None and row.quote_age_seconds <= 3600
            ),
            "stale_age_gt_1h": subgroup(
                lambda row: row.quote_age_seconds is not None and row.quote_age_seconds > 3600
            ),
        },
        "performance_by_threshold_location": {
            "negative_threshold": subgroup(lambda row: row.threshold < 0),
            "zero_to_half_threshold": subgroup(lambda row: 0 <= row.threshold < Decimal("0.5")),
            "half_or_more_threshold": subgroup(lambda row: row.threshold >= Decimal("0.5")),
        },
        "predictor_inventory": {
            "admissible": [
                "prior P8 initial-release values with release_instant strictly before cutoff"
            ],
            "missing": [
                (
                    "independent contemporaneous forecast vintages or survey consensus "
                    "snapshots with exact publication timestamps"
                ),
                "point-in-time release-calendar snapshots for every cutoff",
            ],
        },
        "modelability": "PARTIAL_PREDICTOR_EVIDENCE_REQUIRED",
        "research_only": True,
        "production_influence": "0",
    }
