"""M27N.2 -- end-to-end candidate-evidence packet tests.

Fake-transport-only, no network, no wgrib2 subprocess (the GRIB extraction text is a captured
fixture string, exactly like every other GRIB-parser test in this repository -- see
``tests/test_m27c_weather_calibration_grib.py``'s own module docstring). Proves: a fully
reconstructed typed evidence triple round-trips through the existing, unmodified
``select_experimental_candidate``; the resulting packet never claims more than the three fixed
success markers; an M27D abstention becomes an explicit ``ABSTAIN`` packet, never a hard failure;
and any tampering of upstream evidence (event, market, orderbook) fails closed rather than being
silently absorbed into the packet.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.weather_calibration_grib import parse_wgrib2_max_t_evidence
from services.forecasting.weather_probability import (
    WeatherProbabilityAbstention,
    load_weather_residual_population,
)
from services.market_universe.event_snapshot import acquire_event_snapshot
from services.market_universe.orderbook_snapshot import acquire_orderbook_snapshot
from services.market_universe.pricing import PriceLadder
from services.supervised_canary import m27d as m27d_module
from services.supervised_canary.m27n2_candidate_packet import (
    SUCCESS_MARKERS,
    CandidatePacketState,
    EvidenceProvenance,
    _canonical_packet_hash,
    build_m27n2_candidate_packet,
    validate_m27n2_candidate_packet,
)
from services.supervised_canary.m27n2_evidence_reconstruction import EvidenceReconstructionError
from tests.test_event_snapshot import _fake_transport as _event_fake_transport
from tests.test_m27c_daily_temperature_contract_authority import event as build_event
from tests.test_m27c_daily_temperature_contract_authority import market as build_market
from tests.test_m27c_weather_probability import artifact as population_artifact
from tests.test_m27i_live_weather_preflight import _raw_market, _snapshot_payload  # noqa: F401
from tests.test_orderbook_snapshot import _fake_transport as _orderbook_fake_transport
from tests.test_orderbook_snapshot import _orderbooks_payload

MARKET_TICKER = "KXHIGHAUS-26AUG15-B100.5"
EVENT_TICKER = "KXHIGHAUS-26AUG15"
SERIES_TICKER = "KXHIGHAUS"
NOW = datetime(2026, 8, 20, 3, 10, 0, tzinfo=UTC)
FORBIDDEN_LABELS = ("READY_TO_TRADE", "AUTHORIZED", "APPROVED", "LIVE_READY", "SAFE_TO_SUBMIT")
_LADDER_RANGES = [{"start": "0.0000", "end": "1.0000", "step": ".001"}]


def _ladder() -> PriceLadder:
    return PriceLadder.parse("deci_cent", _LADDER_RANGES)


def _extraction_2026() -> str:
    """A captured wgrib2 extraction fixture for an in-window (August 2026) 03Z MaxT cycle --
    same grid/value shape as ``tests/test_m27c_weather_calibration_grib.py``'s own fixture,
    dated so the resulting local target date falls inside M27D's August-only canary window."""
    sections = []
    for number, start, end, value in (
        (1, "20260820120000", "20260821000000", "302"),
        (2, "20260821120000", "20260822000000", "307"),
        (3, "20260822120000", "20260823000000", "309.3"),
    ):
        sections.append(
            f"""record {number}:
reference = 20260820030000
variable = TMAX
level = 2 m above ground
generating_process = 2
statistical_process = 2
time_processing = 2
parameter = 0/0/4
unit = Kelvin
start = {start}
end = {end}
verification = {end}
lat = 41.794091
lon = 272.260017
value = {value}
grid_template = 30
nx = 2145
ny = 1377
dx = 2539.703
dy = 2539.703
"""
        )
    return "\n".join(sections)


def _raw_chicago_market(*, floor: object = 84) -> dict[str, object]:
    return build_market(
        measurement="maximum",
        location="Chicago",
        identifier="CLIMDW",
        strike_type="greater",
        floor=floor,
        cap=None,
        date_text="Aug 20, 2026",
    ).raw


def _raw_chicago_event() -> dict[str, object]:
    return build_event(event_ticker=EVENT_TICKER, series_ticker=SERIES_TICKER).raw


def _event_snapshot_payload(raw_event: dict[str, object]) -> dict[str, object]:
    payload = {"event": raw_event}
    transport = _event_fake_transport(ticker=EVENT_TICKER, payload=payload, observed_at=NOW)
    return acquire_event_snapshot(EVENT_TICKER, transport=transport, clock=lambda: NOW).to_json()


def _market_snapshot_payload(raw_market: dict[str, object]) -> dict[str, object]:
    return _snapshot_payload(NOW, ticker=MARKET_TICKER, raw_market=raw_market, observed_at=NOW)


def _orderbook_snapshot_payload(*, yes: str = "0.100", no: str = "0.850") -> dict[str, object]:
    payload = _orderbooks_payload(MARKET_TICKER, yes=((yes, "5"),), no=((no, "5"),))
    transport = _orderbook_fake_transport(ticker=MARKET_TICKER, payload=payload, observed_at=NOW)
    snapshot = acquire_orderbook_snapshot(MARKET_TICKER, transport=transport, clock=lambda: NOW)
    return snapshot.to_json()


def _series_fee_payload(*, ticker: str = SERIES_TICKER) -> dict[str, object]:
    return {
        "ticker": ticker,
        "title": "T",
        "category": "Weather",
        "frequency": "daily",
        "tags": [],
        "settlement_sources": [],
        "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": "1",
        "last_updated_ts": (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }


def _build(
    *,
    floor: object = 84,
    yes: str = "0.050",
    no: str = "0.900",
    event_snapshot_payload: object | None = None,
    market_snapshot_payload: object | None = None,
    orderbook_snapshot_payload: object | None = None,
    series_fee_payload: object | None = None,
    event_fee_payload: object | None = None,
):
    raw_grib = parse_wgrib2_max_t_evidence(
        _extraction_2026(), raw_grib_sha256="raw-2026", extraction_sha256="extraction-2026"
    )
    ladder = _ladder()
    return build_m27n2_candidate_packet(
        now=NOW,
        evidence_provenance=EvidenceProvenance.LOCAL_TEST_FIXTURE,
        event_snapshot_payload=(
            event_snapshot_payload
            if event_snapshot_payload is not None
            else _event_snapshot_payload(_raw_chicago_event())
        ),
        market_snapshot_payload=(
            market_snapshot_payload
            if market_snapshot_payload is not None
            else _market_snapshot_payload(_raw_chicago_market(floor=floor))
        ),
        orderbook_snapshot_payload=(
            orderbook_snapshot_payload
            if orderbook_snapshot_payload is not None
            else _orderbook_snapshot_payload(yes=yes, no=no)
        ),
        expected_market_ticker=MARKET_TICKER,
        expected_event_ticker=EVENT_TICKER,
        series_ticker=SERIES_TICKER,
        raw_grib_evidence=raw_grib,
        forecast_record_number=1,
        population_artifact_payload=population_artifact(lead=54_000),
        population_exact_midpoint_seconds=54_000,
        population_training_start=date(2024, 1, 1),
        population_training_end=date(2025, 6, 30),
        market_source_id="m27n2-test-market-source",
        ladder=ladder,
        orderbook_source_id="m27n2-test-book",
        series_fee_payload=(
            series_fee_payload if series_fee_payload is not None else _series_fee_payload()
        ),
        event_fee_payload=event_fee_payload if event_fee_payload is not None else {},
        requested_quantity=Decimal("1.00"),
        economics_observed_at=NOW,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def _population_model_identity() -> str:
    """The exact model identity a synthetic test population artifact honestly recomputes to --
    never one of the three real, hardcoded ``FROZEN_MODEL_IDENTITIES`` (those are fixed hashes
    of the actual historical training data M27D requires, which no test fixture can reproduce).
    Tests that want to reach ``CANDIDATE_QUALIFIED`` patch the frozen set to include this value,
    so they prove the reconstruction *wiring* is correct without claiming a fixture is real
    trained evidence -- exactly the LOCAL_TEST_FIXTURE distinction this module's own
    ``EvidenceProvenance`` field exists to keep honest."""
    population = load_weather_residual_population(
        population_artifact(lead=54_000),
        exact_midpoint_seconds=54_000,
        training_start=date(2024, 1, 1),
        training_end=date(2025, 6, 30),
    )
    assert not isinstance(population, WeatherProbabilityAbstention)
    return population.identity.model_id


def test_full_reconstruction_qualifies_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(m27d_module.FROZEN_MODEL_IDENTITIES, 54_000, _population_model_identity())
    packet = _build()

    assert packet.state == CandidatePacketState.CANDIDATE_QUALIFIED.value
    assert packet.qualified is True
    assert packet.abstain_reason is None
    assert packet.market_ticker == MARKET_TICKER
    assert packet.event_ticker == EVENT_TICKER
    assert packet.series_ticker == SERIES_TICKER
    assert packet.success_markers == SUCCESS_MARKERS
    assert packet.evidence_provenance == EvidenceProvenance.LOCAL_TEST_FIXTURE.value
    assert Decimal(packet.research_probability_discrepancy or "0") > 0
    round_trip = validate_m27n2_candidate_packet(packet.to_json())
    assert round_trip.valid is True, round_trip.reason


def test_packet_round_trips_through_independent_validation() -> None:
    packet = _build()
    result = validate_m27n2_candidate_packet(packet.to_json())
    assert result.valid is True, result.reason


def test_packet_content_hash_changes_with_content() -> None:
    packet = _build()
    tampered = packet.to_json()
    tampered["market_ticker"] = "SOMETHING-ELSE"
    result = validate_m27n2_candidate_packet(tampered)
    assert result.valid is False
    assert "content hash" in (result.reason or "")


def test_success_markers_never_contain_forbidden_authorization_labels() -> None:
    for packet in (_build(), _build(yes="0.020", no="0.050")):
        rendered = json.dumps(packet.to_json())
        for forbidden in FORBIDDEN_LABELS:
            assert forbidden not in rendered


# ---------------------------------------------------------------------------
# Explicit abstention -- never a hard failure
# ---------------------------------------------------------------------------


def test_abstains_when_no_qualifying_candidate_exists() -> None:
    """An expensive book (price already near the research probability) never qualifies M27D's
    20-point discrepancy threshold -- this must surface as an explicit ABSTAIN, not an error."""
    packet = _build(yes="0.020", no="0.050")

    assert packet.state == CandidatePacketState.ABSTAIN.value
    assert packet.qualified is False
    assert packet.candidate_id is None
    assert packet.abstain_reason
    assert packet.success_markers == SUCCESS_MARKERS


def test_abstains_when_population_has_insufficient_training_data() -> None:
    packet = _build()
    thin_population = population_artifact(lead=54_000, count=10)
    packet = build_m27n2_candidate_packet(
        now=NOW,
        evidence_provenance=EvidenceProvenance.LOCAL_TEST_FIXTURE,
        event_snapshot_payload=_event_snapshot_payload(_raw_chicago_event()),
        market_snapshot_payload=_market_snapshot_payload(_raw_chicago_market()),
        orderbook_snapshot_payload=_orderbook_snapshot_payload(),
        expected_market_ticker=MARKET_TICKER,
        expected_event_ticker=EVENT_TICKER,
        series_ticker=SERIES_TICKER,
        raw_grib_evidence=parse_wgrib2_max_t_evidence(
            _extraction_2026(), raw_grib_sha256="raw-2026", extraction_sha256="extraction-2026"
        ),
        forecast_record_number=1,
        population_artifact_payload=thin_population,
        population_exact_midpoint_seconds=54_000,
        population_training_start=date(2024, 1, 1),
        population_training_end=date(2025, 6, 30),
        market_source_id="m27n2-test-market-source",
        ladder=_ladder(),
        orderbook_source_id="m27n2-test-book",
        series_fee_payload=_series_fee_payload(),
        event_fee_payload={},
        requested_quantity=Decimal("1.00"),
        economics_observed_at=NOW,
    )
    assert packet.state == CandidatePacketState.ABSTAIN.value
    assert "population abstained" in (packet.abstain_reason or "")


# ---------------------------------------------------------------------------
# Fail-closed on tampered upstream evidence
# ---------------------------------------------------------------------------


def test_raises_on_event_snapshot_that_does_not_validate() -> None:
    tampered = dict(_event_snapshot_payload(_raw_chicago_event()))
    tampered["ticker"] = "WRONG"
    with pytest.raises(EvidenceReconstructionError):
        _build(event_snapshot_payload=tampered)


def test_raises_on_market_snapshot_that_does_not_validate() -> None:
    tampered = dict(_market_snapshot_payload(_raw_chicago_market()))
    tampered["ticker"] = "WRONG"
    with pytest.raises(EvidenceReconstructionError):
        _build(market_snapshot_payload=tampered)


def test_raises_on_orderbook_snapshot_that_does_not_validate() -> None:
    tampered = dict(_orderbook_snapshot_payload())
    tampered["ticker"] = "WRONG"
    with pytest.raises(EvidenceReconstructionError):
        _build(orderbook_snapshot_payload=tampered)


def test_rejects_evidence_provenance_that_is_not_an_explicit_enum_member() -> None:
    raw_grib = parse_wgrib2_max_t_evidence(
        _extraction_2026(), raw_grib_sha256="raw-2026", extraction_sha256="extraction-2026"
    )
    with pytest.raises(EvidenceReconstructionError, match="EvidenceProvenance"):
        build_m27n2_candidate_packet(
            now=NOW,
            evidence_provenance="LOCAL_TEST_FIXTURE",  # type: ignore[arg-type]
            event_snapshot_payload=_event_snapshot_payload(_raw_chicago_event()),
            market_snapshot_payload=_market_snapshot_payload(_raw_chicago_market()),
            orderbook_snapshot_payload=_orderbook_snapshot_payload(),
            expected_market_ticker=MARKET_TICKER,
            expected_event_ticker=EVENT_TICKER,
            series_ticker=SERIES_TICKER,
            raw_grib_evidence=raw_grib,
            forecast_record_number=1,
            population_artifact_payload=population_artifact(lead=54_000),
            population_exact_midpoint_seconds=54_000,
            population_training_start=date(2024, 1, 1),
            population_training_end=date(2025, 6, 30),
            market_source_id="m27n2-test-market-source",
            ladder=_ladder(),
            orderbook_source_id="m27n2-test-book",
            series_fee_payload=_series_fee_payload(),
            event_fee_payload={},
            requested_quantity=Decimal("1.00"),
            economics_observed_at=NOW,
        )


# ---------------------------------------------------------------------------
# Fee-series identity binding (M27N.2 corrective pass, section 1)
# ---------------------------------------------------------------------------


def test_rejects_fee_payload_for_a_different_valid_series() -> None:
    """A fee observation that is itself perfectly well-formed -- just for a different, unrelated
    series -- must never silently supply this market's fee policy. Attacks exactly the scenario
    named in the corrective-pass instructions: expected series KXHIGHAUS, fee payload series a
    different valid series."""
    mismatched = _series_fee_payload(ticker="KXHIGHNYC")
    with pytest.raises(EvidenceReconstructionError, match="does not match the expected series"):
        _build(series_fee_payload=mismatched)


def test_fee_series_mismatch_fails_before_any_economics_are_computed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The series-ticker cross-check must happen before the mismatched fee evidence can influence
    anything -- assert build_authoritative_market_economics is never even reached."""
    from services.supervised_canary import m27n2_candidate_packet as packet_module

    def _must_not_be_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("build_authoritative_market_economics must not be reached")

    monkeypatch.setattr(packet_module, "build_authoritative_market_economics", _must_not_be_called)
    mismatched = _series_fee_payload(ticker="KXHIGHNYC")
    with pytest.raises(EvidenceReconstructionError, match="does not match the expected series"):
        _build(series_fee_payload=mismatched)


def test_accepts_fee_payload_with_exact_matching_series_ticker() -> None:
    """The positive case: an exactly matching series ticker proceeds through reconstruction
    (matches every other happy-path test's implicit default, asserted explicitly here)."""
    packet = _build(series_fee_payload=_series_fee_payload(ticker=SERIES_TICKER))
    assert packet.state in {
        CandidatePacketState.CANDIDATE_QUALIFIED.value,
        CandidatePacketState.ABSTAIN.value,
    }


# ---------------------------------------------------------------------------
# content_hash trust-model boundary (M27N.2 corrective pass, section 2)
# ---------------------------------------------------------------------------


def test_tampered_primary_evidence_never_reaches_a_content_hash_at_all() -> None:
    """content_hash is never an input to reconstruction -- it is only ever computed, inside
    __post_init__, from whatever fields a *successful* build already produced. Tampering the
    primary evidence (not the serialized packet) is caught by the upstream frozen validators
    before any packet -- and therefore any content_hash -- exists to tamper with. There is no
    "supply a pre-computed content_hash to get past validation" attack against
    build_m27n2_candidate_packet, because it accepts no such parameter."""
    tampered = dict(_market_snapshot_payload(_raw_chicago_market()))
    tampered["ticker"] = "WRONG-MARKET"
    with pytest.raises(EvidenceReconstructionError):
        _build(market_snapshot_payload=tampered)


def test_recomputed_content_hash_on_fabricated_evidence_does_not_confer_authenticity() -> None:
    """Demonstrates -- rather than merely asserting in a docstring -- the exact limitation
    documented on validate_m27n2_candidate_packet: it is a mutation-detection check over a
    payload's own claimed fields, not a provenance/authenticity check. An attacker who fabricates
    a plausible-looking payload (never having gone through build_m27n2_candidate_packet or any of
    the frozen validators/builders it calls) and correctly recomputes content_hash with the exact
    same public recipe this module uses will still pass this structural check. This is expected
    and by design (no signing/trust-root capability is authorized for this module) -- which is
    exactly why nothing anywhere in this codebase ever treats a "validated" (i.e.
    self-consistent) M27N2CandidatePacket payload as authoritative on its own: the only source of
    truth for any packet's claims is a fresh call to build_m27n2_candidate_packet against real,
    independently-validated primary evidence -- see the module and validate_m27n2_candidate_packet
    docstrings."""
    real_packet = _build()
    fabricated = dict(real_packet.to_json())
    fabricated["market_ticker"] = "TOTALLY-FABRICATED-MARKET"
    fabricated["candidate_id"] = "fabricated-by-an-attacker-who-never-touched-real-evidence"
    fabricated["content_hash"] = _canonical_packet_hash(fabricated)

    result = validate_m27n2_candidate_packet(fabricated)

    assert result.valid is True, (
        "structural self-consistency alone cannot and must not be mistaken for authenticity -- "
        "see the documented content_hash trust model"
    )


# ---------------------------------------------------------------------------
# Packet validator robustness against malformed success_markers (M27N.2 second
# corrective pass, section 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed_markers",
    [5, True, None, {}, "OFFLINE_CANDIDATE_EVIDENCE_REVALIDATED"],
    ids=["int", "bool", "none", "empty-dict", "bare-string"],
)
def test_validate_packet_fails_closed_on_malformed_success_markers_without_raising(
    malformed_markers: object,
) -> None:
    """None of these shapes may ever reach ``tuple(...)`` uncaught -- each must return
    ``PacketValidation(False, ...)`` instead of letting a ``TypeError`` escape
    ``validate_m27n2_candidate_packet``. Covers exactly the five shapes named in the corrective-pass
    instructions: a bare int, a bool (a non-iterable ``int`` subclass -- the actual original crash
    case), ``None``, an empty dict, and a plausible-looking bare string."""
    real_packet = _build()
    tampered = dict(real_packet.to_json())
    tampered["success_markers"] = malformed_markers
    tampered["content_hash"] = _canonical_packet_hash(tampered)

    result = validate_m27n2_candidate_packet(tampered)

    assert result.valid is False
    assert result.reason


def test_validate_packet_rejects_bare_string_success_markers_without_character_iteration() -> None:
    """A bare string is itself iterable character-by-character -- it must be rejected outright as
    the wrong shape (a specific, distinct reason), never silently exploded into a tuple of single
    characters and compared against the fixed marker tuple."""
    real_packet = _build()
    tampered = dict(real_packet.to_json())
    tampered["success_markers"] = "".join(SUCCESS_MARKERS)  # plausible-looking bare string
    tampered["content_hash"] = _canonical_packet_hash(tampered)

    result = validate_m27n2_candidate_packet(tampered)

    assert result.valid is False
    assert "list of marker strings" in (result.reason or "")


def test_validate_packet_rejects_a_list_of_the_wrong_marker_strings() -> None:
    """The exact-schema requirement: a well-shaped list (not a crash risk) that simply does not
    contain the fixed reviewed marker set must still fail closed, with the *set-mismatch* reason,
    distinct from the *wrong-shape* reason the malformed-type cases above return."""
    real_packet = _build()
    tampered = dict(real_packet.to_json())
    tampered["success_markers"] = ["READY_TO_TRADE"]
    tampered["content_hash"] = _canonical_packet_hash(tampered)

    result = validate_m27n2_candidate_packet(tampered)

    assert result.valid is False
    assert "fixed reviewed set" in (result.reason or "")


def test_validate_packet_still_accepts_the_exact_well_formed_success_markers() -> None:
    """Regression guard: the malformed-input fix must not disturb the well-formed case."""
    real_packet = _build()
    result = validate_m27n2_candidate_packet(real_packet.to_json())
    assert result.valid is True, result.reason


# ---------------------------------------------------------------------------
# Fee-evidence parse-failure normalization (M27N.2 second corrective pass, section 2)
# ---------------------------------------------------------------------------


def test_malformed_series_fee_payload_raises_evidence_reconstruction_error() -> None:
    """``CurrentSeriesFeeObservation.parse``'s own ``OpportunityError`` (e.g. a missing ``ticker``)
    must never leak past ``reconstruct_economics``'s public boundary as a bare
    ``OpportunityError`` -- it must become ``EvidenceReconstructionError``, exactly like every
    other failure path through this function."""
    malformed = dict(_series_fee_payload())
    del malformed["ticker"]
    with pytest.raises(EvidenceReconstructionError, match="series fee evidence failed to parse"):
        _build(series_fee_payload=malformed)


def test_malformed_event_fee_override_raises_evidence_reconstruction_error() -> None:
    """``EventFeeOverride.parse``'s own ``OpportunityError`` (a partial override -- a type without
    its matching multiplier) must also become ``EvidenceReconstructionError``, not a bare
    ``OpportunityError``."""
    with pytest.raises(EvidenceReconstructionError, match="event fee override failed to parse"):
        _build(event_fee_payload={"fee_type_override": "quadratic_with_maker_fees"})


def test_valid_fee_evidence_still_reconstructs_successfully_after_the_error_wrapping_fix() -> None:
    """Regression guard: wrapping the two fee parsers' exceptions must not disturb the well-formed
    happy path -- well-formed series/event fee evidence still reconstructs all the way through."""
    packet = _build(
        series_fee_payload=_series_fee_payload(ticker=SERIES_TICKER), event_fee_payload={}
    )
    assert packet.state in {
        CandidatePacketState.CANDIDATE_QUALIFIED.value,
        CandidatePacketState.ABSTAIN.value,
    }


def test_wrong_series_fee_payload_still_fails_before_economics_after_the_error_wrapping_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the error-wrapping fix must not disturb or reorder the already-fixed
    fee-series binding check -- a wrong-but-well-formed series ticker must still be rejected by the
    series-identity cross-check (a plain ``EvidenceReconstructionError``, not the parse-failure
    wrapper), and must still never reach ``build_authoritative_market_economics``."""
    from services.supervised_canary import m27n2_candidate_packet as packet_module

    def _must_not_be_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("build_authoritative_market_economics must not be reached")

    monkeypatch.setattr(packet_module, "build_authoritative_market_economics", _must_not_be_called)
    mismatched = _series_fee_payload(ticker="KXHIGHNYC")
    with pytest.raises(EvidenceReconstructionError, match="does not match the expected series"):
        _build(series_fee_payload=mismatched)
