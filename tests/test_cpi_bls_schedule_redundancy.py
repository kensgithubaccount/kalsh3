from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.forecasting.cpi_bls_schedule_redundancy import (
    BLSHTTPObservation,
    BLSProbeEndpoint,
    BLSScheduleTarget,
    build_bls_probe_endpoints,
    probe_bls_schedule_redundancy,
)

TARGET = BLSScheduleTarget(
    reference_year=2026,
    reference_month=10,
    release_at_utc=datetime(2026, 11, 10, 13, 30, tzinfo=UTC),
)
OBSERVED = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)


def _body(endpoint: BLSProbeEndpoint, *, wrong_date: bool = False) -> bytes:
    date = "November 11, 2026" if wrong_date else "November 10, 2026"
    if endpoint.authority_mode == "CPI_SCHEDULE":
        return (
            "<html><title>Schedule of Releases for the Consumer Price Index</title>"
            f"<body>October 2026 {date} 08:30 AM</body></html>"
        ).encode()
    if endpoint.authority_mode == "MONTHLY_LIST":
        return (
            "<html><body>"
            f"Tuesday, {date} 08:30 AM Consumer Price Index for October 2026"
            "</body></html>"
        ).encode()
    return (
        "<html><body>The Consumer Price Index for October 2026 is scheduled "
        f"to be released on {date}, at 8:30 A.M. Eastern Time.</body></html>"
    ).encode()


def _observation(
    endpoint: BLSProbeEndpoint,
    *,
    status: int = 200,
    wrong_date: bool = False,
) -> BLSHTTPObservation:
    return BLSHTTPObservation(
        status=status,
        observed_at_utc=OBSERVED,
        body=_body(endpoint, wrong_date=wrong_date),
    )


def test_target_normalizes_to_utc_and_eastern_release_clock() -> None:
    assert TARGET.release_at_utc.isoformat() == "2026-11-10T13:30:00+00:00"
    assert TARGET.release_local.isoformat().startswith("2026-11-10T08:30:00-05:00")
    assert TARGET.reference_label == "October 2026"


def test_probe_requires_multiple_authority_valid_first_party_paths() -> None:
    def fetcher(endpoint: BLSProbeEndpoint, timeout_seconds: float) -> BLSHTTPObservation:
        assert timeout_seconds == 10.0
        return _observation(endpoint)

    report = probe_bls_schedule_redundancy(
        TARGET,
        collector_label="unit-test",
        fetcher=fetcher,
    )
    assert len(report.results) == 4
    assert report.transport_success_count == 4
    assert report.authority_valid_count == 4
    assert report.path_redundancy_ready is True
    assert report.as_dict()["scientific_evidence_authorized"] is False


def test_probe_preserves_every_403_and_does_not_short_circuit() -> None:
    endpoints = build_bls_probe_endpoints(TARGET)
    calls: list[str] = []

    def fetcher(endpoint: BLSProbeEndpoint, _timeout_seconds: float) -> BLSHTTPObservation:
        calls.append(endpoint.name)
        if endpoint == endpoints[0]:
            return _observation(endpoint)
        return _observation(endpoint, status=403)

    report = probe_bls_schedule_redundancy(
        TARGET,
        collector_label="blocked-egress",
        fetcher=fetcher,
    )
    assert calls == [endpoint.name for endpoint in endpoints]
    assert report.transport_success_count == 1
    assert report.authority_valid_count == 1
    assert report.path_redundancy_ready is False
    assert [result.http_status for result in report.results[1:]] == [403, 403, 403]


def test_http_200_with_wrong_release_date_is_not_authority_valid() -> None:
    first = build_bls_probe_endpoints(TARGET)[0]

    def fetcher(endpoint: BLSProbeEndpoint, _timeout_seconds: float) -> BLSHTTPObservation:
        return _observation(endpoint, wrong_date=endpoint == first)

    report = probe_bls_schedule_redundancy(
        TARGET,
        collector_label="authority-check",
        required_valid_paths=4,
        fetcher=fetcher,
    )
    assert report.transport_success_count == 4
    assert report.authority_valid_count == 3
    assert report.path_redundancy_ready is False
    assert report.results[0].diagnostic.startswith("authority mismatch:")


def test_probe_rejects_blank_collector_and_impossible_path_threshold() -> None:
    with pytest.raises(ValueError, match="collector_label"):
        probe_bls_schedule_redundancy(TARGET, collector_label=" ")
    with pytest.raises(ValueError, match="required_valid_paths"):
        probe_bls_schedule_redundancy(
            TARGET,
            collector_label="unit-test",
            required_valid_paths=5,
        )


def test_report_never_serializes_raw_response_bytes() -> None:
    def fetcher(endpoint: BLSProbeEndpoint, _timeout_seconds: float) -> BLSHTTPObservation:
        return _observation(endpoint)

    report = probe_bls_schedule_redundancy(
        TARGET,
        collector_label="unit-test",
        fetcher=fetcher,
    ).as_dict()
    serialized = repr(report)
    assert "raw_body" not in serialized
    assert "Consumer Price Index for October 2026" not in serialized
