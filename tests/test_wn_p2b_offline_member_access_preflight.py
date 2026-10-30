"""Offline no-go geometry checks: no forecast bytes, GCS, EE, or quote requests."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime
from typing import Any

import pytest

import scripts.wn_p2b_offline_member_access_preflight as preflight
from scripts.audit_wn_p1_source_metadata import CITY_HOURS
from scripts.wn_p2b_offline_member_access_preflight import (
    NO_GO,
    MemberAccessPreflightError,
    plan_global_member_access_no_go,
)

TARGET = date(2026, 11, 1)
EXPECTED_CITY_LEADS = {
    "boston": (6, 12, 18, 24, 30),
    "miami": (6, 12, 18, 24, 30),
    "denver": (12, 18, 24, 30),
    "los_angeles": (12, 18, 24, 30, 36),
    "seattle": (12, 18, 24, 30, 36),
}


def test_complete_five_city_fixed_windows_and_deduplicated_leads() -> None:
    report = plan_global_member_access_no_go(TARGET)
    assert len(report.cities) == 5
    assert tuple(city.city for city in report.cities) == tuple(sorted(EXPECTED_CITY_LEADS))
    assert report.required_lead_endpoints == (6, 12, 18, 24, 30, 36)
    for city in report.cities:
        assert city.six_hour_lead_endpoints == EXPECTED_CITY_LEADS[city.city]
        assert len(city.valid_hour_offsets) == 24
        assert city.valid_hour_offsets == tuple(
            range(city.valid_hour_offsets[0], city.valid_hour_offsets[0] + 24)
        )
        assert city.global_member_lead_chunks == 64 * len(city.six_hour_lead_endpoints)
    assert report.cities[0].valid_hour_offsets[0] == 5
    assert report.cities[-1].valid_hour_offsets[-1] == 31


def test_global_chunks_are_unique_cross_product_of_all_leads_and_64_members() -> None:
    report = plan_global_member_access_no_go(TARGET)
    keys = report.required_global_member_lead_keys
    assert len(keys) == 384
    assert len(set(keys)) == 384
    assert set(keys) == {(member, lead) for member in range(64) for lead in (6, 12, 18, 24, 30, 36)}
    assert len({lead for _, lead in keys}) == 6
    assert len({member for member, _ in keys}) == 64


def test_decoded_global_geometry_is_not_network_observation_or_bill() -> None:
    report = plan_global_member_access_no_go(TARGET)
    assert report.decoded_bytes_per_global_chunk == 622_252_800
    assert report.total_global_decoded_bytes == 238_945_075_200
    assert report.inherited_reader_network_cap_bytes == 2 * 1024**3
    assert report.measured_network_transfer_bytes is None
    assert report.measured_network_request_count is None
    assert report.observed_source_generation is None
    assert report.observed_source_before_decision is False
    assert report.source_access_demonstrated is False


def test_every_result_denies_source_model_station_paper_and_trading_authority() -> None:
    report = plan_global_member_access_no_go(TARGET)
    assert report.decision == NO_GO
    assert report.policy == preflight.POLICY
    assert report.model_probability_authorized is False
    assert report.settlement_station_authorized is False
    assert report.paper_authorized is False
    assert report.trading_authority == "NONE"
    assert report.research_only is True
    assert all(not hasattr(report, value) for value in ("p_yes", "fair_price", "order_id"))


def test_source_path_is_hypothetical_exact_00z_object_not_proof_of_existence() -> None:
    report = plan_global_member_access_no_go(date(2026, 11, 2))
    assert report.hypothetical_source_object == (
        "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/"
        "20261102_00hr_XX_preds/predictions.zarr/"
    )
    assert report.observed_source_generation is None
    assert report.source_access_demonstrated is False


@pytest.mark.parametrize(
    "target",
    [date(2026, 10, 31), date(2100, 1, 1), datetime(2026, 11, 1), "2026-11-01", None],
)
def test_out_of_scope_target_cannot_create_plan(target: object) -> None:
    with pytest.raises(MemberAccessPreflightError):
        plan_global_member_access_no_go(target)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "invalid"),
    [
        ("MEMBER_COUNT", 63),
        ("DATA_CHUNKS", (1, 1, 1, 3601, 7200)),
        ("CHUNK_DECODED_BYTES", 1),
        ("MAX_TRANSFER_BYTES", 999),
    ],
)
def test_changed_source_layout_or_transfer_cap_fails_closed(
    monkeypatch: pytest.MonkeyPatch, name: str, invalid: object
) -> None:
    monkeypatch.setattr(preflight, name, invalid)
    with pytest.raises(MemberAccessPreflightError):
        plan_global_member_access_no_go(TARGET)


def test_changed_fixed_standard_time_windows_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    altered = dict(CITY_HOURS)
    altered["denver"] = (8, 31)
    monkeypatch.setattr(preflight, "CITY_HOURS", altered)
    with pytest.raises(MemberAccessPreflightError, match="five-city windows"):
        plan_global_member_access_no_go(TARGET)


def test_no_constructible_positive_authority_fields() -> None:
    report = plan_global_member_access_no_go(TARGET)
    stored = {field.name for field in fields(report)}
    for key in (
        "source_access_demonstrated",
        "observed_source_before_decision",
        "model_probability_authorized",
        "paper_authorized",
        "trading_authority",
        "decision",
    ):
        assert key not in stored
    # This hostile runtime call must bypass mypy's static dataclass keyword check.
    invalid_replace: Any = replace
    with pytest.raises(TypeError):
        invalid_replace(report, source_access_demonstrated=True)
    with pytest.raises(AttributeError):
        object.__setattr__(report, "paper_authorized", True)


def test_constructible_fake_geometry_cannot_grant_authority() -> None:
    report = plan_global_member_access_no_go(TARGET)
    forged: dict[str, Any] = {field.name: getattr(report, field.name) for field in fields(report)}
    forged["total_global_decoded_bytes"] = 0
    fake = preflight.OfflineMemberAccessNoGo(**forged)
    assert fake.total_global_decoded_bytes == 0
    assert fake.source_access_demonstrated is False
    assert fake.measured_network_transfer_bytes is None
    assert fake.model_probability_authorized is False
    assert fake.paper_authorized is False
    assert fake.decision == NO_GO
