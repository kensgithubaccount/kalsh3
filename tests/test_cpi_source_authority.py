from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pytest

from services.forecasting.cpi_source_authority import (
    BLS_CPI_PRODUCT,
    BLS_ORGANIZATION,
    POLICY_IDENTITY,
    POLICY_ROWS,
    SOURCE_AUTHORITIES,
    CPISourceAuthorityError,
    CPISourceInterface,
    CPISourceProfile,
    CPISourceRole,
    ReviewedCPISourceAuthority,
    resolve_cpi_source_authority,
    source_policy_identity,
    validate_cpi_source_authority,
)
from services.market_universe.domain import stable_hash

PROFILE = CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE
DOCUMENT = CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT
CALENDAR = CPISourceRole.RELEASE_CALENDAR
DOCUMENT_URL = "https://www.bls.gov/news.release/archives/cpi_08122026.htm"
CALENDAR_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"


def resolve_document() -> ReviewedCPISourceAuthority:
    return resolve_cpi_source_authority(profile=PROFILE, role=DOCUMENT, locator=DOCUMENT_URL)


def test_positive_policy_is_exactly_two_research_only_zero_influence_roles() -> None:
    assert len(POLICY_ROWS) == len(SOURCE_AUTHORITIES) == 2
    assert {row[3] for row in POLICY_ROWS} == {DOCUMENT, CALENDAR}
    assert {row[0] for row in POLICY_ROWS} == {PROFILE}
    for authority in SOURCE_AUTHORITIES.values():
        validate_cpi_source_authority(authority)
        assert authority.source_organization == BLS_ORGANIZATION
        assert authority.source_product == BLS_CPI_PRODUCT
        assert authority.policy_identity == POLICY_IDENTITY
        assert authority.research_only is True
        assert authority.production_influence == Decimal("0")


def test_exact_reviewed_locators_resolve_without_io() -> None:
    document = resolve_document()
    calendar = resolve_cpi_source_authority(profile=PROFILE, role=CALENDAR, locator=CALENDAR_URL)
    assert document.source_interface is CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML
    assert document.locator_shape == ("https://www.bls.gov/news.release/archives/cpi_MMDDYYYY.htm")
    assert calendar.source_interface is CPISourceInterface.BLS_CPI_RELEASE_SCHEDULE_HTML
    assert "printed" in document.evidentiary_role
    assert "scheduled" in calendar.evidentiary_role


def test_ordinary_public_construction_and_reconstruction_are_rejected() -> None:
    with pytest.raises(CPISourceAuthorityError):
        ReviewedCPISourceAuthority(profile=PROFILE)  # type: ignore[call-arg]
    forged = object.__new__(ReviewedCPISourceAuthority)
    with pytest.raises(CPISourceAuthorityError):
        validate_cpi_source_authority(forged)


def test_dataclasses_replace_cannot_create_changed_canonical_authority() -> None:
    with pytest.raises(CPISourceAuthorityError):
        replace(resolve_document(), source_product="forged")


def test_object_setattr_mutation_and_rehash_fail_canonical_validation() -> None:
    authority = resolve_document()
    original_product = authority.source_product
    original_identity = authority.authority_identity
    try:
        object.__setattr__(authority, "source_product", "forged BLS product")
        object.__setattr__(
            authority,
            "authority_identity",
            stable_hash((POLICY_IDENTITY, "forged BLS product")),
        )
        with pytest.raises(CPISourceAuthorityError):
            validate_cpi_source_authority(authority)
        with pytest.raises(CPISourceAuthorityError):
            resolve_document()
    finally:
        object.__setattr__(authority, "source_product", original_product)
        object.__setattr__(authority, "authority_identity", original_identity)
    validate_cpi_source_authority(authority)


def test_plain_strings_cannot_substitute_for_exact_strenum_identity() -> None:
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(  # type: ignore[arg-type]
            profile=PROFILE.value,
            role=DOCUMENT,
            locator=DOCUMENT_URL,
        )
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(  # type: ignore[arg-type]
            profile=PROFILE,
            role=DOCUMENT.value,
            locator=DOCUMENT_URL,
        )


@pytest.mark.parametrize(
    "locator",
    [
        "BLS",
        "bls.gov",
        "https://www.bls.gov",
        "https://www.bls.gov/cpi/",
        "http://www.bls.gov/news.release/archives/cpi_08122026.htm",
        "https://bls.gov/news.release/archives/cpi_08122026.htm",
        "https://download.bls.gov/news.release/archives/cpi_08122026.htm",
        "https://api.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0",
        "https://www.bls.gov/publicAPI/v2/timeseries/data/CUSR0000SA0",
        "https://www.bls.gov/news.release/archives/cpi_08122026.pdf",
        "https://www.bls.gov/news.release/cpi.nr0.htm",
        "https://www.bls.gov/news.release/archives/ppi_08122026.htm",
        "https://www.bls.gov/news.release/archives/cpi_13322026.htm",
        "https://www.bls.gov/news.release/archives/cpi_08122026.htm?x=1",
        "https://www.bls.gov/news.release/archives/cpi_08122026.htm#top",
        "https://www.bls.gov/news.release/archives/cpi_08122026.htm?",
        "https://www.bls.gov/news.release/archives/cpi_08122026.htm#",
        "https://www.bls.gov/news.release/\narchives/cpi_08122026.htm",
        "https://www.bls.gov/news.release/\rarchives/cpi_08122026.htm",
        "https://www.bls.gov/news.release/\tarchives/cpi_08122026.htm",
        "https://www.bls.gov//news.release/archives/cpi_08122026.htm",
        "https://www.bls.gov/news.release/archives/%63pi_08122026.htm",
        "https://user@www.bls.gov/news.release/archives/cpi_08122026.htm",
        "https://www.bls.gov:443/news.release/archives/cpi_08122026.htm",
    ],
)
def test_initial_release_document_locator_fails_closed(locator: str) -> None:
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(profile=PROFILE, role=DOCUMENT, locator=locator)


@pytest.mark.parametrize(
    "locator",
    [
        "https://www.bls.gov/schedule/",
        "https://www.bls.gov/schedule/news_release/bls.ics",
        "https://www.bls.gov/schedule/news_release/ppi.htm",
        "https://www.bls.gov/schedule/news_release/cpi.htm?source=tracking",
        "https://www.bls.gov/schedule/news_release/cpi.htm?",
        "https://www.bls.gov/schedule/news_release/cpi.htm#",
        "https://www.bls.gov/schedule/news_release/\ncpi.htm",
        "https://www.bls.gov/schedule/news_release/\rcpi.htm",
        "https://www.bls.gov/schedule/news_release/\tcpi.htm",
        "http://www.bls.gov/schedule/news_release/cpi.htm",
        "https://bls.gov/schedule/news_release/cpi.htm",
    ],
)
def test_release_calendar_locator_fails_closed(locator: str) -> None:
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(profile=PROFILE, role=CALENDAR, locator=locator)


def test_source_roles_cannot_masquerade_for_each_other() -> None:
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(profile=PROFILE, role=CALENDAR, locator=DOCUMENT_URL)
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(profile=PROFILE, role=DOCUMENT, locator=CALENDAR_URL)


@pytest.mark.parametrize("series", ["KXCPIYOY", "KXCPICORE", "KXCPICOREYOY"])
def test_related_cpi_series_cannot_inherit_profile(series: str) -> None:
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(  # type: ignore[arg-type]
            profile=series,
            role=DOCUMENT,
            locator=DOCUMENT_URL,
        )


def test_kxcpi_ticker_title_category_family_and_release_target_are_not_authority() -> None:
    calls: tuple[dict[str, Any], ...] = (
        {"ticker": "KXCPI"},
        {"title": "CPI above 0.2%"},
        {"category": "Economics"},
        {"family": "BINARY_THRESHOLD"},
        {"release_target": "CPI"},
    )
    for kwargs in calls:
        with pytest.raises(TypeError):
            resolve_cpi_source_authority(**kwargs)  # type: ignore[arg-type]


class OtherProfile(StrEnum):
    CORE = "cpi-u-us-city-average-all-items-sa-one-month-percent-change-initial-release"


class EqualText(str):
    pass


def test_unsupported_profile_even_with_equal_string_value_cannot_resolve() -> None:
    with pytest.raises(CPISourceAuthorityError):
        resolve_cpi_source_authority(  # type: ignore[arg-type]
            profile=OtherProfile.CORE,
            role=DOCUMENT,
            locator=DOCUMENT_URL,
        )


def test_caller_selected_authority_or_provenance_identity_is_rejected() -> None:
    with pytest.raises(TypeError):
        resolve_cpi_source_authority(  # type: ignore[call-arg]
            profile=PROFILE,
            role=DOCUMENT,
            locator=DOCUMENT_URL,
            authority_identity="caller-selected",
        )
    with pytest.raises(CPISourceAuthorityError):
        ReviewedCPISourceAuthority(  # type: ignore[call-arg]
            _capability=object(),
            profile=PROFILE,
            authority_identity="caller-selected",
        )


def test_altered_role_profile_interface_locator_shape_and_product_fail_validation() -> None:
    authority = resolve_document()
    fields: tuple[tuple[str, object], ...] = (
        ("source_role", CALENDAR),
        ("profile", PROFILE.value),
        ("source_interface", CPISourceInterface.BLS_CPI_RELEASE_SCHEDULE_HTML),
        ("locator_shape", "https://www.bls.gov/anything"),
        ("source_product", "Consumer Price Index database"),
    )
    for name, changed in fields:
        original = getattr(authority, name)
        try:
            object.__setattr__(authority, name, changed)
            with pytest.raises(CPISourceAuthorityError):
                validate_cpi_source_authority(authority)
        finally:
            object.__setattr__(authority, name, original)
    validate_cpi_source_authority(authority)


def test_equal_valued_noncanonical_runtime_types_fail_validation() -> None:
    authority = resolve_document()
    fields: tuple[tuple[str, object], ...] = (
        ("research_only", 1),
        ("production_influence", 0),
        ("source_organization", EqualText(BLS_ORGANIZATION)),
    )
    for name, changed in fields:
        original = getattr(authority, name)
        assert changed == original
        try:
            object.__setattr__(authority, name, changed)
            with pytest.raises(CPISourceAuthorityError):
                validate_cpi_source_authority(authority)
        finally:
            object.__setattr__(authority, name, original)
    validate_cpi_source_authority(authority)


def test_policy_identity_is_deterministic_and_caller_changes_do_not_mint_authority() -> None:
    assert (
        source_policy_identity(POLICY_ROWS)
        == POLICY_IDENTITY
        == source_policy_identity(POLICY_ROWS)
    )
    changed = list(POLICY_ROWS)
    row = list(changed[0])
    row[2] = "Consumer Price Index database"
    changed[0] = tuple(row)  # type: ignore[assignment]
    with pytest.raises(CPISourceAuthorityError):
        source_policy_identity(tuple(changed))  # type: ignore[arg-type]


def test_research_only_and_production_influence_cannot_be_weakened() -> None:
    authority = resolve_document()
    for name, changed in (("research_only", False), ("production_influence", Decimal("1"))):
        original = getattr(authority, name)
        try:
            object.__setattr__(authority, name, changed)
            with pytest.raises(CPISourceAuthorityError):
                validate_cpi_source_authority(authority)
        finally:
            object.__setattr__(authority, name, original)


def test_evidentiary_boundaries_leave_pit_settlement_and_gates_ungranted() -> None:
    document = resolve_document()
    calendar = resolve_cpi_source_authority(profile=PROFILE, role=CALENDAR, locator=CALENDAR_URL)
    assert "Kalshi settlement truth" in document.does_not_prove
    assert "published_at" in document.does_not_prove
    assert "replay_available_at" in document.does_not_prove
    assert "the released CPI value" in calendar.does_not_prove
    for gate in ("G1", "G2", "G3", "G4", "G5", "G6"):
        assert any(item.startswith(gate) for item in document.does_not_prove)


def test_module_has_no_empirical_acquisition_gate_model_or_execution_dependencies() -> None:
    import services.forecasting.cpi_source_authority as module

    source = inspect.getsource(module)
    forbidden = (
        "requests",
        "httpx",
        "urllib.request",
        "services.market_universe.empirical_researchability",
        "services.market_universe.modelability",
        "services.historical_replay",
        "ReleaseVintage",
        "Availability",
        "TradeCandidate",
        "DecisionReceipt",
        "RiskIntent",
        "production_execution",
        "account",
        "credential",
        "signer",
        "order",
    )
    assert all(value not in source for value in forbidden)
