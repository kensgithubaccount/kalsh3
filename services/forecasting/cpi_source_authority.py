"""Reviewed BLS source authority for the first CPI initial-release research profile.

CPI-E1-P1 is source governance only.  This module performs no I/O, acquires no
empirical corpus, and grants no settlement, point-in-time, gate, modelability,
or production authority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit

from services.market_universe.domain import stable_hash

POLICY_VERSION = "cpi-e1-p1-bls-source-governance-v1"
BLS_HTTPS_ORIGIN = "https://www.bls.gov"
BLS_ORGANIZATION = "U.S. Bureau of Labor Statistics"
BLS_CPI_PRODUCT = "Consumer Price Index news release"
ZERO = Decimal("0")
_AUTHORITY_CAPABILITY = object()
_ARCHIVED_RELEASE = re.compile(r"/news\.release/archives/cpi_([0-9]{8})\.htm\Z")


class CPISourceAuthorityError(ValueError):
    """Raised when CPI source authority cannot be proven exactly."""


class CPISourceProfile(StrEnum):
    """The sole source product/profile reviewed by CPI-E1-P1."""

    CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE = (
        "cpi-u-us-city-average-all-items-sa-one-month-percent-change-initial-release"
    )


class CPISourceRole(StrEnum):
    HISTORICAL_INITIAL_RELEASE_DOCUMENT = "historical-initial-release-document"
    RELEASE_CALENDAR = "release-calendar"


class CPISourceInterface(StrEnum):
    BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML = "bls-archived-cpi-news-release-html"
    BLS_CPI_RELEASE_SCHEDULE_HTML = "bls-cpi-release-schedule-html"


PolicyRow = tuple[
    CPISourceProfile,
    str,
    str,
    CPISourceRole,
    CPISourceInterface,
    str,
    str,
    tuple[str, ...],
]

_DOES_NOT_PROVE_COMMON = (
    "Kalshi settlement truth",
    "G1 exact settlement/domain binding",
    "G2 gate PASS",
    "G3 historical settlement truth",
    "G4 original-vintage/PIT availability",
    "G5 evidence-unit sufficiency",
    "G6 economics",
)

POLICY_ROWS: tuple[PolicyRow, ...] = (
    (
        CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE,
        BLS_ORGANIZATION,
        BLS_CPI_PRODUCT,
        CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT,
        CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML,
        f"{BLS_HTTPS_ORIGIN}/news.release/archives/cpi_YYYYMMDD.htm",
        (
            "eligible evidence for the CPI-U U.S. city average all-items seasonally adjusted "
            "signed one-month percentage change printed in that archived release; release "
            "material may also be inspected later for an exact embargo/release instant"
        ),
        (
            *_DOES_NOT_PROVE_COMMON,
            "published_at",
            "replay_available_at",
            "actual first-public server time",
            "original acquisition time",
            "revision_number or revision lineage",
        ),
    ),
    (
        CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE,
        BLS_ORGANIZATION,
        BLS_CPI_PRODUCT,
        CPISourceRole.RELEASE_CALENDAR,
        CPISourceInterface.BLS_CPI_RELEASE_SCHEDULE_HTML,
        f"{BLS_HTTPS_ORIGIN}/schedule/news_release/cpi.htm",
        "eligible evidence for the BLS-scheduled CPI release date and time",
        (
            *_DOES_NOT_PROVE_COMMON,
            "the released CPI value",
            "actual first-public server time",
            "the actual release time if publication was delayed or rescheduled",
            "published_at or replay_available_at",
        ),
    ),
)


def source_policy_identity(rows: tuple[PolicyRow, ...]) -> str:
    """Content-address validated source-policy rows without granting authority."""
    _validate_policy_rows(rows)
    return stable_hash((POLICY_VERSION, rows))


def _validate_policy_rows(rows: tuple[PolicyRow, ...]) -> None:
    if not isinstance(rows, tuple) or len(rows) != 2:
        raise CPISourceAuthorityError("CPI source policy must contain exactly two reviewed rows")
    seen_roles: set[CPISourceRole] = set()
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 8:
            raise CPISourceAuthorityError("malformed CPI source-policy row")
        profile, organization, product, role, interface, shape, may_prove, does_not_prove = row
        if type(profile) is not CPISourceProfile:
            raise CPISourceAuthorityError("CPI source profile must have exact enum identity")
        if type(role) is not CPISourceRole:
            raise CPISourceAuthorityError("CPI source role must have exact enum identity")
        if type(interface) is not CPISourceInterface:
            raise CPISourceAuthorityError("CPI source interface must have exact enum identity")
        if profile is not CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE:
            raise CPISourceAuthorityError("unsupported CPI source profile")
        if organization != BLS_ORGANIZATION or product != BLS_CPI_PRODUCT:
            raise CPISourceAuthorityError("unreviewed CPI source organization or product")
        if not isinstance(shape, str) or not shape:
            raise CPISourceAuthorityError("missing CPI source locator shape")
        if not isinstance(may_prove, str) or not may_prove:
            raise CPISourceAuthorityError("missing CPI evidentiary role")
        if (
            not isinstance(does_not_prove, tuple)
            or not does_not_prove
            or any(not isinstance(item, str) or not item for item in does_not_prove)
        ):
            raise CPISourceAuthorityError("missing CPI negative-evidence boundary")
        if role in seen_roles:
            raise CPISourceAuthorityError("duplicate CPI source role")
        seen_roles.add(role)
    if seen_roles != {
        CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT,
        CPISourceRole.RELEASE_CALENDAR,
    }:
        raise CPISourceAuthorityError("CPI source policy role coverage failure")


POLICY_IDENTITY = source_policy_identity(POLICY_ROWS)


@dataclass(frozen=True, slots=True, init=False)
class ReviewedCPISourceAuthority:
    profile: CPISourceProfile
    source_organization: str
    source_product: str
    source_role: CPISourceRole
    source_interface: CPISourceInterface
    locator_shape: str
    evidentiary_role: str
    does_not_prove: tuple[str, ...]
    authority_identity: str
    policy_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _AUTHORITY_CAPABILITY:
            raise CPISourceAuthorityError(
                "reviewed CPI source authority is not caller-constructible"
            )
        expected = set(self.__dataclass_fields__)
        if set(values) != expected:
            raise CPISourceAuthorityError(
                "reviewed CPI source authority fields are issuer-controlled"
            )
        for name, value in values.items():
            object.__setattr__(self, name, value)


def _authority_identity(row: PolicyRow) -> str:
    return stable_hash(("cpi-e1-p1-source-authority-v1", POLICY_IDENTITY, row))


def _build_authorities() -> Mapping[
    tuple[CPISourceProfile, CPISourceRole], ReviewedCPISourceAuthority
]:
    values: dict[tuple[CPISourceProfile, CPISourceRole], ReviewedCPISourceAuthority] = {}
    for row in POLICY_ROWS:
        profile, organization, product, role, interface, shape, may_prove, does_not_prove = row
        key = (profile, role)
        values[key] = ReviewedCPISourceAuthority(
            _capability=_AUTHORITY_CAPABILITY,
            profile=profile,
            source_organization=organization,
            source_product=product,
            source_role=role,
            source_interface=interface,
            locator_shape=shape,
            evidentiary_role=may_prove,
            does_not_prove=does_not_prove,
            authority_identity=_authority_identity(row),
            policy_identity=POLICY_IDENTITY,
            research_only=True,
            production_influence=ZERO,
        )
    return MappingProxyType(values)


SOURCE_AUTHORITIES = _build_authorities()


def validate_cpi_source_authority(authority: ReviewedCPISourceAuthority) -> None:
    """Revalidate an issued authority against fixed reviewed policy semantics."""
    if type(authority) is not ReviewedCPISourceAuthority:
        raise CPISourceAuthorityError("CPI source authority must have exact issued type")
    profile = getattr(authority, "profile", None)
    role = getattr(authority, "source_role", None)
    interface = getattr(authority, "source_interface", None)
    if type(profile) is not CPISourceProfile:
        raise CPISourceAuthorityError("CPI source profile must have exact enum identity")
    if type(role) is not CPISourceRole:
        raise CPISourceAuthorityError("CPI source role must have exact enum identity")
    if type(interface) is not CPISourceInterface:
        raise CPISourceAuthorityError("CPI source interface must have exact enum identity")
    canonical = SOURCE_AUTHORITIES.get((profile, role))
    if canonical is not authority:
        raise CPISourceAuthorityError("unissued or reconstructed CPI source authority")
    row = _row_for(profile, role)
    expected = (
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
        row[6],
        row[7],
        _authority_identity(row),
        POLICY_IDENTITY,
        True,
        ZERO,
    )
    actual = (
        authority.profile,
        authority.source_organization,
        authority.source_product,
        authority.source_role,
        authority.source_interface,
        authority.locator_shape,
        authority.evidentiary_role,
        authority.does_not_prove,
        authority.authority_identity,
        authority.policy_identity,
        authority.research_only,
        authority.production_influence,
    )
    if actual != expected:
        raise CPISourceAuthorityError("CPI source authority failed canonical revalidation")


def resolve_cpi_source_authority(
    *, profile: CPISourceProfile, role: CPISourceRole, locator: str
) -> ReviewedCPISourceAuthority:
    """Resolve only an exact reviewed CPI profile/role and exact BLS locator shape."""
    if type(profile) is not CPISourceProfile:
        raise CPISourceAuthorityError("CPI source profile must have exact enum identity")
    if type(role) is not CPISourceRole:
        raise CPISourceAuthorityError("CPI source role must have exact enum identity")
    if not isinstance(locator, str) or not locator or locator != locator.strip():
        raise CPISourceAuthorityError("CPI source locator must be an exact non-empty string")
    authority = SOURCE_AUTHORITIES.get((profile, role))
    if authority is None:
        raise CPISourceAuthorityError("unsupported CPI source profile or role")
    validate_cpi_source_authority(authority)
    _validate_locator(authority.source_interface, locator)
    return authority


def _row_for(profile: CPISourceProfile, role: CPISourceRole) -> PolicyRow:
    rows = [row for row in POLICY_ROWS if row[0] is profile and row[3] is role]
    if len(rows) != 1:
        raise CPISourceAuthorityError("CPI source policy lookup is not unique")
    return rows[0]


def _validate_locator(interface: CPISourceInterface, locator: str) -> None:
    parsed = urlsplit(locator)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.bls.gov"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CPISourceAuthorityError("CPI source locator origin is not exactly reviewed")
    if interface is CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML:
        match = _ARCHIVED_RELEASE.fullmatch(parsed.path)
        if match is None:
            raise CPISourceAuthorityError(
                "CPI release document path is not the reviewed archive shape"
            )
        stamp = match.group(1)
        try:
            date(int(stamp[4:8]), int(stamp[0:2]), int(stamp[2:4]))
        except ValueError as exc:
            raise CPISourceAuthorityError("CPI release document date is invalid") from exc
        return
    if interface is CPISourceInterface.BLS_CPI_RELEASE_SCHEDULE_HTML:
        if parsed.path != "/schedule/news_release/cpi.htm":
            raise CPISourceAuthorityError("CPI release-calendar path is not exactly reviewed")
        return
    raise CPISourceAuthorityError("unreviewed CPI source interface")
