"""A0.5-S2B-P5 forensic root-cause and repair proof.

Rows 193-205 of the live a02-prospective-20260906 observation store recorded
ABSTAIN / STRUCTURAL_SIGNAL_ENVELOPE_UNAVAILABLE for every one of 13 structural
candidates spanning six unrelated event families, all with
evidence.detail == "reviewed structural semantic normalization is not valid".

Forensic tracing (capture_structural_observation -> build_structural_signal_envelope
-> _semantic_specification -> ContractSpecificationParser.parse) found the immediate
cause: `ContractSpecification.strategy_supported` requires
`semantic_status == VALID`, and every one of the sampled real markets independently
failed the parser's TIMEZONE_AMBIGUITY gate, because the live Kalshi market/event
schema never publishes a MARKET.timezone or EVENT.timezone field at all (verified by
reading, strictly read-only, the exact real market/event/series payloads for these
tickers out of the point-in-time archive at
/Users/ksyme/.local/state/kalsh3/a02-prospective-20260906/market-universe/archive.sqlite3).
Every real payload reproduced below is trimmed to the fields
`ContractSpecificationParser.parse` reads, taken verbatim from that archive.

Root cause classification: INPUT-BINDING BUG. `parse_time` already requires an
explicit UTC offset on MARKET.deadline/MARKET.expiration_time and always resolves
it to UTC (services/market_universe/domain.py::parse_time) -- the deadline's own
already-validated explicit offset is positive evidence of the applicable timezone;
the parser was simply never reading it, only ever the separate "timezone" field
that the live schema never sends.

The fix is intentionally NOT applied inside `ContractSpecificationParser` itself:
that shared parser is also used, unmodified, by the frozen CPI P9A/P10x historical
replay path (services/historical_replay/cpi_price_evidence.py), whose committed
`semantic_hash` values were computed under the original behavior. Changing the
parser's own timezone derivation would silently invalidate that separate frozen
evidence. Instead, the derivation is applied only at the prospective S2B call site
(`_prospective_market_with_derived_timezone`, services/prospective_shadow/kernel.py),
on a copy of the market mapping, before it is handed to the unmodified parser.
"timezone" is not a member of RULE_FIELDS/METADATA_FIELDS
(services/market_universe/domain.py), so this cannot affect
`authority.market.rules_hash`/`metadata_hash`.

Every one of the three families below ALSO independently fails a second, unrelated,
and NOT repaired gate: UNKNOWN_LANGUAGE, because the deterministic comparison-phrase
templates in `_COMPARISON_TEMPLATES` only recognize a narrow set of literal
CPI-report and weather-station phrasings and were never extended to the generic
"If <subject> <comparator> <threshold>[, unit], then the market resolves to Yes."
shape used across most of the catalog. That gap is a genuine, separate parser
coverage gap -- for at least KXGOVTCUTS the correct semantics ("N below a reference
baseline level") cannot even be safely represented by the current single
comparator/single-threshold model without risking an inverted comparator polarity --
so it is intentionally left fail-closed and unrepaired here (see the P5 forensic
report). These tests prove the timezone repair is real, isolated, and does not
silently paper over that separate gap: all three real fixtures below correctly
remain blocked (ABSTAIN) after the fix, for the correct remaining reason.

No test in this file performs live network acquisition; all fixtures are static,
already-persisted, point-in-time evidence.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from services.contract_intelligence.specification import (
    ContractSpecificationParser,
    IssueType,
    SemanticsInputBundle,
)
from services.market_universe.archive import EntityKind
from services.market_universe.domain import Series, material_hashes
from services.prospective_shadow.kernel import (
    HydratedMarketAuthority,
    HydratedSeriesAuthority,
    ShadowKernelError,
    _prospective_market_with_derived_timezone,
    _semantic_specification,
    canonical_json,
)

NOW = datetime(2026, 9, 9, 19, 53, tzinfo=UTC)

# --- KXARTISTSTREAMSY: real payload for KXARTISTSTREAMSY-ODDMOB26DEC31-120.0M ---
ARTIST_STREAMS_MARKET = {
    "ticker": "KXARTISTSTREAMSY-ODDMOB26DEC31-120.0M",
    "event_ticker": "KXARTISTSTREAMSY-ODDMOB26DEC31",
    "title": (
        "Will Odd Mob have 120000000 Streams on Luminate during January 01 - December 31, 2026?"
    ),
    "yes_sub_title": "Above 120M",
    "no_sub_title": "Above 120M",
    "rules_primary": (
        "If Odd Mob has Above 120M Worldwide Streams during the "
        "January 01 - December 31, 2026 period, then the market resolves to Yes."
    ),
    "rules_secondary": (
        "Data for these Contracts will be based on the January 01 - December 31, 2026 period."
    ),
    "floor_strike": 120000000,
    "strike_type": "greater",
    "expiration_time": "2027-01-05T15:00:00Z",
    "expected_expiration_time": "2027-01-04T15:00:00Z",
    "occurrence_datetime": "2027-01-01T15:00:00Z",
    "status": "active",
    # NOTE: no "timezone" key -- verified absent in the real archived payload.
}
ARTIST_STREAMS_EVENT = {
    "event_ticker": "KXARTISTSTREAMSY-ODDMOB26DEC31",
    "series_ticker": "KXARTISTSTREAMSY",
    "category": "Entertainment",
    "title": "Odd Mob Streams in 2026",
    "settlement_sources": [{"name": "Luminate", "url": "https://luminatedata.com/"}],
    "mutually_exclusive": False,
}
ARTIST_STREAMS_SERIES = {
    "ticker": "KXARTISTSTREAMSY",
    "title": "Artist Yearly Streams",
    "category": "Entertainment",
    "frequency": "annual",
    "settlement_sources": [{"name": "Luminate", "url": "https://luminatedata.com/"}],
}

# --- KXGOVTCUTS: real payload for KXGOVTCUTS-28-50 ---
GOVTCUTS_MARKET = {
    "ticker": "KXGOVTCUTS-28-50",
    "event_ticker": "KXGOVTCUTS-28",
    "title": "Will government spending decrease by 50 before 2028?",
    "yes_sub_title": "At least 50 billion",
    "no_sub_title": "At least 50 billion",
    "rules_primary": (
        "If government spending (FGEXPND) is at least $50 billion below the Q4 "
        "2024 level in any quarter through Q4 2028, then the market resolves to Yes."
    ),
    "rules_secondary": "Each quarter from Q1 2025 to Q4 2028 is compared against Q4 2024.",
    "floor_strike": 50,
    "strike_type": "greater",
    "expiration_time": "2029-03-31T15:00:00Z",
    "expected_expiration_time": "2029-03-31T15:00:00Z",
    "occurrence_datetime": "2029-03-31T15:00:00Z",
    "status": "active",
    # NOTE: no "timezone" key -- verified absent in the real archived payload.
}
GOVTCUTS_EVENT = {
    "event_ticker": "KXGOVTCUTS-28",
    "series_ticker": "KXGOVTCUTS",
    "category": "Economics",
    "title": "How much government spending will Trump cut before his term ends?",
    "settlement_sources": [{"name": "FRED", "url": "https://fred.stlouisfed.org/series/FGEXPND"}],
    "mutually_exclusive": False,
}
GOVTCUTS_SERIES = {
    "ticker": "KXGOVTCUTS",
    "title": "Government budget cuts",
    "category": "Economics",
    "frequency": "custom",
    "settlement_sources": [{"name": "FRED", "url": "https://fred.stlouisfed.org/series/FGEXPND"}],
}

# --- KXMPOXCOUNT: real payload for KXMPOXCOUNT-27JAN01-A35 ---
MPOXCOUNT_MARKET = {
    "ticker": "KXMPOXCOUNT-27JAN01-A35",
    "event_ticker": "KXMPOXCOUNT-27JAN01",
    "title": "Above 35 mpox clade Ib cases in the United States during 2026",
    "yes_sub_title": "Above 35",
    "no_sub_title": "Above 35",
    "rules_primary": (
        "If the total number of mpox clade Ib cases reported in the United "
        "States during 2026 is above 35, then the market resolves to Yes."
    ),
    "rules_secondary": "Cases are determined at 10:00 AM ET on the Expiration Date (January 8).",
    "floor_strike": 35,
    "strike_type": "greater",
    "expiration_time": "2027-01-08T15:00:00Z",
    "expected_expiration_time": "2027-01-08T15:00:00Z",
    "occurrence_datetime": "2027-01-01T15:00:00Z",
    "status": "active",
    # NOTE: no "timezone" key -- verified absent in the real archived payload.
}
MPOXCOUNT_EVENT = {
    "event_ticker": "KXMPOXCOUNT-27JAN01",
    "series_ticker": "KXMPOXCOUNT",
    "category": "Science and Technology",
    "title": "How many mpox clade Ib cases will be reported in the United States in 2026?",
    "settlement_sources": [
        {"name": "national health agencies", "url": "https://kalshi.com/"},
        {"name": "CDC", "url": "https://www.cdc.gov/index.html"},
    ],
    "mutually_exclusive": False,
}
MPOXCOUNT_SERIES = {
    "ticker": "KXMPOXCOUNT",
    "title": "U.S. clade I mpox cases",
    "category": "Science and Technology",
    "frequency": "one_off",
    "settlement_sources": [
        {"name": "national health agencies", "url": "https://kalshi.com/"},
        {"name": "CDC", "url": "https://www.cdc.gov/index.html"},
    ],
}

REAL_FAMILIES = {
    "KXARTISTSTREAMSY": (ARTIST_STREAMS_MARKET, ARTIST_STREAMS_EVENT, ARTIST_STREAMS_SERIES),
    "KXGOVTCUTS": (GOVTCUTS_MARKET, GOVTCUTS_EVENT, GOVTCUTS_SERIES),
    "KXMPOXCOUNT": (MPOXCOUNT_MARKET, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES),
}


def _authority(market_raw: dict, event_raw: dict) -> HydratedMarketAuthority:
    rules_hash, metadata_hash = material_hashes(market_raw)
    market = SimpleNamespace(
        ticker=market_raw["ticker"],
        event_ticker=market_raw["event_ticker"],
        rules_hash=rules_hash,
        metadata_hash=metadata_hash,
        raw=market_raw,
    )
    event = SimpleNamespace(
        ticker=event_raw["event_ticker"],
        series_ticker=event_raw["series_ticker"],
        metadata_hash="4" * 64,
        raw=event_raw,
    )
    market_snapshot = SimpleNamespace(body_sha256="5" * 64, observed_at=NOW - timedelta(seconds=1))
    event_snapshot = SimpleNamespace(body_sha256="6" * 64)
    return HydratedMarketAuthority(market, event, market_snapshot, event_snapshot)


def _series_authority(series_raw: dict) -> HydratedSeriesAuthority:
    series = Series.parse(series_raw)
    observation = SimpleNamespace(
        kind=EntityKind.SERIES,
        ticker=series_raw["ticker"],
        canonical_source_hash=hashlib.sha256(canonical_json(series_raw).encode()).hexdigest(),
        metadata_hash=series.metadata_hash,
        observation_id="8" * 64,
        acquired_at=NOW - timedelta(seconds=3),
        parser_version="m2-market-universe-parser-v1",
        archive_schema_version="m26f-universe-archive-schema-v1",
        archive_policy_version="m26f-canonical-json-sha256-v1",
        entity=series,
    )
    return HydratedSeriesAuthority(series, observation)


def test_real_candidate_families_no_longer_hit_timezone_ambiguity_at_kernel_boundary() -> None:
    """The specific, proven root cause (no MARKET/EVENT.timezone in the live
    schema) is fixed, at the prospective S2B call site, for three materially
    different real event families -- and still fails closed overall for the
    correct, separate, unrepaired reason (UNKNOWN_LANGUAGE)."""
    for name, (market_raw, event_raw, series_raw) in REAL_FAMILIES.items():
        authority = _authority(market_raw, event_raw)
        series_authority = _series_authority(series_raw)
        try:
            _semantic_specification(authority, series_authority)
        except ShadowKernelError as exc:
            assert str(exc) == "reviewed structural semantic normalization is not valid", name
        else:
            raise AssertionError(f"{name}: expected a fail-closed ShadowKernelError")

        # Confirm *why* it still fails closed: TIMEZONE_AMBIGUITY must be gone,
        # UNKNOWN_LANGUAGE must be the (only newly relevant) remaining reason.
        normalized_market = _prospective_market_with_derived_timezone(market_raw)
        spec = ContractSpecificationParser().parse(
            SemanticsInputBundle.build(normalized_market, dict(event_raw), dict(series_raw)),
            now=NOW,
        )
        issue_types = {x.issue_type for x in spec.issues}
        assert IssueType.TIMEZONE_AMBIGUITY not in issue_types, name
        assert IssueType.UNKNOWN_LANGUAGE in issue_types, name
        assert spec.timezone == "UTC", name


def test_derived_timezone_market_copy_preserves_rules_and_metadata_hash() -> None:
    """Injecting the derived timezone must never perturb the hashes that gate
    exact Market authority -- "timezone" is not in RULE_FIELDS/METADATA_FIELDS."""
    for name, (market_raw, _event_raw, _series_raw) in REAL_FAMILIES.items():
        normalized = _prospective_market_with_derived_timezone(market_raw)
        assert normalized.get("timezone") == "UTC", name
        assert material_hashes(normalized) == material_hashes(market_raw), name


def test_shared_contract_specification_parser_is_untouched() -> None:
    """The fix lives only at the S2B call site. Calling the shared,
    unmodified `ContractSpecificationParser` directly -- exactly as the frozen
    CPI P9A/P10x historical replay path does -- on a payload lacking a
    "timezone" field must still hit TIMEZONE_AMBIGUITY exactly as before this
    repair. This is the isolation guarantee that keeps frozen CPI evidence
    identity untouched."""
    market_raw, event_raw, series_raw = REAL_FAMILIES["KXARTISTSTREAMSY"]
    spec = ContractSpecificationParser().parse(
        SemanticsInputBundle.build(dict(market_raw), dict(event_raw), dict(series_raw)),
        now=NOW,
    )
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}
    assert spec.timezone is None


def test_derivation_does_not_consult_ticker_or_title() -> None:
    """The derived timezone must come only from the already-validated deadline
    offset -- never from ticker text or title heuristics (explicitly prohibited)."""
    market = dict(ARTIST_STREAMS_MARKET)
    market["ticker"] = "ZZZUNKNOWNTICKER-1"
    market["title"] = "completely unrecognizable free text with no timezone words"
    normalized = _prospective_market_with_derived_timezone(market)
    assert normalized["timezone"] == "UTC"


def test_explicit_timezone_field_still_wins_over_derivation() -> None:
    market = dict(ARTIST_STREAMS_MARKET) | {"timezone": "America/New_York"}
    normalized = _prospective_market_with_derived_timezone(market)
    assert normalized["timezone"] == "America/New_York"


def test_unresolvable_deadline_and_absent_timezone_is_not_derived() -> None:
    """No explicit timezone and no resolvable deadline is a genuine ambiguity;
    the market mapping must pass through unchanged and still fail closed."""
    market = dict(ARTIST_STREAMS_MARKET)
    del market["expiration_time"]
    del market["expected_expiration_time"]
    normalized = _prospective_market_with_derived_timezone(market)
    assert "timezone" not in normalized

    authority = _authority(market, ARTIST_STREAMS_EVENT)
    series_authority = _series_authority(ARTIST_STREAMS_SERIES)
    try:
        _semantic_specification(authority, series_authority)
    except ShadowKernelError as exc:
        assert str(exc) == "reviewed structural semantic normalization is not valid"
    else:
        raise AssertionError("expected a fail-closed ShadowKernelError")
    normalized_bundle = ContractSpecificationParser().parse(
        SemanticsInputBundle.build(
            normalized, dict(ARTIST_STREAMS_EVENT), dict(ARTIST_STREAMS_SERIES)
        ),
        now=NOW,
    )
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in normalized_bundle.issues}
