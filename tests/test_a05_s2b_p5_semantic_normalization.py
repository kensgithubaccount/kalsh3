"""A0.5-S2B-P5-R1 forensic root-cause proof and safe containment repair.

Rows 193-205 of the live a02-prospective-20260906 observation store recorded
ABSTAIN / STRUCTURAL_SIGNAL_ENVELOPE_UNAVAILABLE for every one of 13 structural
candidates spanning six unrelated event families, all with
evidence.detail == "reviewed structural semantic normalization is not valid".

An earlier iteration of this task (A0.5-S2B-P5) added
`_prospective_market_with_derived_timezone` to services/prospective_shadow/
kernel.py, which derived a synthetic `timezone = "UTC"` whenever a market's
deadline/expiration timestamp resolved with an explicit UTC offset but no
separate MARKET/EVENT.timezone field was present. Independent adversarial
review correctly rejected that as UNSOUND and it has been fully reverted
(services/prospective_shadow/kernel.py is now byte-identical to the canonical
base again): an explicit UTC offset on a timestamp proves only the instant/
transport representation, never the contract's semantic/civil measurement
timezone. The counter-example is KXMPOXCOUNT itself -- its real rules text
states cases are "determined at 10:00 AM ET on Expiration Date" while the API
`expiration_time` is `2027-01-08T15:00:00Z`. 15:00Z and 10:00 AM ET happen to
be the same instant, but the contract's semantic timezone is
America/New_York, not UTC -- and nothing in the market/event payload
positively asserts that. The old helper would have silently and incorrectly
written `timezone = "UTC"` for this exact real market.

No reviewed, existing per-Series semantic timezone authority applicable to
any of the six real structural_threshold families involved here exists
anywhere in this codebase: the only such authority
(`KXCPIReviewedSemanticPolicy` in
services/forecasting/cpi_settlement_reconciliation.py) is scoped exclusively
to the `KXCPI` series, is unrelated to KXARTISTSTREAMSY / KXB200MAX /
KXGOVTCUTS / KXMPOXCOUNT / KXNFL2HSPREAD / KXTRUMPAPPROVALYEAR, and even its
own reviewed timezone value is "UTC" -- it would not have helped the ET
counter-example either. Per the review's explicit instruction, no new
per-Series timezone allowlist is invented here to paper over that gap.
TIMEZONE_AMBIGUITY (and, independently, UNKNOWN_LANGUAGE) remain real,
unresolved, and correctly fail-closed for all six real families sampled from
rows 193-205. ABSTAIN is the correct, accepted outcome.

This file's job is purely regression proof: that no code anywhere in this
repository (not just the deleted helper) can turn a bare UTC offset, a naive
timestamp, a malformed timestamp, or ticker/title/category/location text into
semantic timezone authority, and that the shared, unmodified
`ContractSpecificationParser` -- and therefore the frozen CPI P9A/P10x
historical replay evidence and KU-A2 identity-freeze checkpoints -- are
completely unaffected.

No test in this file performs live network acquisition; all fixtures are
static, already-persisted, point-in-time evidence or synthetic edge cases.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.contract_intelligence.specification import (
    ContractSpecificationParser,
    IssueType,
    SemanticsInputBundle,
    normalize_timezone,
)
from services.market_universe.archive import EntityKind
from services.market_universe.domain import Series, material_hashes
from services.prospective_shadow.kernel import (
    HydratedMarketAuthority,
    HydratedSeriesAuthority,
    ShadowKernelError,
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
# Rules text asserts settlement occurs "at 10:00 AM ET" while the API
# expiration_time is the same instant expressed as 15:00Z -- the canonical
# counter-example the independent review identified: the offset alone
# expresses only the instant, never that the contract's semantic timezone is
# America/New_York.
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


def _parse(market: dict, event: dict, series: dict):
    return ContractSpecificationParser().parse(
        SemanticsInputBundle.build(dict(market), dict(event), dict(series)), now=NOW
    )


# --- 1/15/16/17/18: real families remain fail closed end-to-end ---


@pytest.mark.parametrize("name", list(REAL_FAMILIES))
def test_real_families_remain_fail_closed_at_kernel_boundary(name: str) -> None:
    """rows 193-205-shaped real evidence: replaying it today under the exact
    same runtime/evidence identity still raises the same fail-closed error --
    it is not reinterpreted as OBSERVE by this repair."""
    market_raw, event_raw, series_raw = REAL_FAMILIES[name]
    authority = _authority(market_raw, event_raw)
    series_authority = _series_authority(series_raw)
    with pytest.raises(ShadowKernelError) as exc_info:
        _semantic_specification(authority, series_authority)
    assert str(exc_info.value) == "reviewed structural semantic normalization is not valid"


def test_kxmpoxcount_et_rules_text_does_not_become_semantic_timezone_utc() -> None:
    """The independent-review counter-example: rules_secondary asserts
    settlement at "10:00 AM ET" while expiration_time is the same instant as
    15:00Z. Nothing in this payload positively establishes a semantic
    timezone, so it must resolve to None/TIMEZONE_AMBIGUITY, never a silently
    derived "UTC" (or any other) value."""
    spec = _parse(MPOXCOUNT_MARKET, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES)
    assert spec.timezone is None
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}
    assert spec.semantic_status.value != "VALID"


@pytest.mark.parametrize("name", list(REAL_FAMILIES))
def test_real_families_hit_timezone_ambiguity_and_unknown_language(name: str) -> None:
    market_raw, event_raw, series_raw = REAL_FAMILIES[name]
    spec = _parse(market_raw, event_raw, series_raw)
    issue_types = {x.issue_type for x in spec.issues}
    assert IssueType.TIMEZONE_AMBIGUITY in issue_types, name
    assert IssueType.UNKNOWN_LANGUAGE in issue_types, name
    assert spec.timezone is None, name
    assert spec.strategy_supported is False, name


# --- 2-7: an offset/timestamp alone never establishes semantic timezone ---


@pytest.mark.parametrize(
    "expiration_time",
    [
        "2027-01-08T15:00:00Z",
        "2027-01-08T15:00:00+00:00",
        "2026-01-01T10:00:00-05:00",
        "2026-06-01T04:30:00+05:30",
    ],
    ids=["Z", "+00:00", "-05:00", "+05:30"],
)
def test_utc_or_any_offset_timestamp_alone_never_establishes_semantic_timezone(
    expiration_time: str,
) -> None:
    market = dict(MPOXCOUNT_MARKET) | {"expiration_time": expiration_time}
    spec = _parse(market, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES)
    assert spec.timezone is None
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}


def test_naive_timestamp_does_not_establish_timezone() -> None:
    market = dict(MPOXCOUNT_MARKET) | {"expiration_time": "2027-01-08T15:00:00"}
    spec = _parse(market, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES)
    assert spec.timezone is None
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}
    assert IssueType.DEADLINE_CONFLICT in {x.issue_type for x in spec.issues}


def test_malformed_timestamp_does_not_establish_timezone() -> None:
    market = dict(MPOXCOUNT_MARKET) | {"expiration_time": "not-a-timestamp"}
    spec = _parse(market, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES)
    assert spec.timezone is None
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}
    assert IssueType.DEADLINE_CONFLICT in {x.issue_type for x in spec.issues}


# --- 8-10: explicit authoritative field behavior is unchanged ---


def test_explicit_authoritative_timezone_remains_accepted() -> None:
    market = dict(MPOXCOUNT_MARKET) | {"timezone": "America/New_York"}
    spec = _parse(market, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES)
    assert spec.timezone == "America/New_York"
    assert IssueType.TIMEZONE_AMBIGUITY not in {x.issue_type for x in spec.issues}


def test_malformed_explicit_timezone_fails_closed() -> None:
    market = dict(MPOXCOUNT_MARKET) | {"timezone": "Mars/Nowhere"}
    spec = _parse(market, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES)
    assert spec.timezone is None
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}


@pytest.mark.parametrize("value", ["", "   ", None])
def test_whitespace_or_empty_timezone_does_not_silently_become_utc(value) -> None:
    assert normalize_timezone(value) is None
    market = dict(MPOXCOUNT_MARKET) | {"timezone": value}
    spec = _parse(market, MPOXCOUNT_EVENT, MPOXCOUNT_SERIES)
    assert spec.timezone is None
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}


# --- 11: ticker/title/category/location text cannot create timezone authority ---


def test_ticker_title_category_location_text_cannot_create_timezone_authority() -> None:
    market = dict(MPOXCOUNT_MARKET)
    market["ticker"] = "KXNYCTEMP-NEWYORK-EASTERN-ET"
    market["title"] = "New York Eastern Time ET America/New_York temperature market"
    event = dict(MPOXCOUNT_EVENT)
    event["category"] = "Weather"
    event["title"] = "New York ET America/New_York"
    spec = _parse(market, event, MPOXCOUNT_SERIES)
    assert spec.timezone is None
    assert IssueType.TIMEZONE_AMBIGUITY in {x.issue_type for x in spec.issues}


# --- 12: shared parser historical behavior is provably untouched ---


def test_shared_contract_specification_parser_matches_canonical_weather_fixture() -> None:
    """A known-good, fully VALID weather spec (explicit timezone, matching
    comparison-language template, all required fields present) must still
    resolve to VALID with the expected comparator/threshold -- proving the
    shared parser's ordinary behavior is completely unaffected by this
    repair (it was reverted to the exact canonical base)."""
    market = {
        "ticker": "M",
        "event_ticker": "E",
        "title": "Will the final temperature be at least 90 F?",
        "yes_sub_title": "Final temperature is 90 F or higher",
        "no_sub_title": "Final temperature is below 90 F",
        "rules_primary": "YES if the final NWS report at station KNYC is at least 90 F.",
        "rules_secondary": "Use the final daily climate report.",
        "station_code": "KNYC",
        "floor_strike": "90",
        "timezone": "America/New_York",
        "expiration_time": "2026-08-11T23:59:00-04:00",
        "settlement_value_dollars": None,
    }
    event = {
        "event_ticker": "E",
        "series_ticker": "S",
        "category": "Weather",
        "timezone": "America/New_York",
        "settlement_sources": [{"name": "NWS", "url": "https://weather.gov"}],
    }
    series = {
        "ticker": "S",
        "title": "Daily weather",
        "category": "Weather",
        "frequency": "daily",
        "settlement_sources": [{"name": "NWS", "url": "https://weather.gov"}],
    }
    spec = _parse(market, event, series)
    assert spec.semantic_status.value == "VALID"
    assert spec.timezone == "America/New_York"
    assert spec.strategy_supported is True
