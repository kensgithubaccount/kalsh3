"""Current-cycle raw-GRIB acquisition layer -- fake-transport-only tests.

No network, no Kalshi imports, no credentials, no market economics, no execution, no subprocess/
wgrib2 invocation. Proves: happy-path acquisition/validation round-trips; every listed rejection
(ambiguous source objects, redirects/wrong host, oversized response, malformed/empty response,
future timestamp, stale cycles) fails closed; and this module never claims DAILY_MAX/KMDW/date/
midpoint/model-identity semantics.
"""

from __future__ import annotations

import ast
import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from services.forecasting.weather_current_cycle_acquisition import (
    AWS_HOST,
    SourceAcquisitionError,
    acquire_current_cycle_raw_grib,
    aws_index_url,
    object_url,
    select_candidate_object_name,
    validate_acquired_forecast_source,
)

NOW = datetime(2026, 8, 20, 3, 30, 0, tzinfo=UTC)
DAY = date(2026, 8, 20)
GOOD_NAME = "YGUZ98_KWBN_2026082002"
RAW_GRIB_BODY = b"GRIB" + b"\x00" * 60  # structurally GRIB2-shaped stub; no real decode attempted


def _index_xml(names: tuple[str, ...]) -> bytes:
    keys = "".join(f"<Contents><Key>wmo/maxt/2026/08/20/{name}</Key></Contents>" for name in names)
    return (
        b'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="x">'
        + keys.encode()
        + b"</ListBucketResult>"
    )


def _fake_transport(
    *,
    index_body: bytes,
    object_body: bytes = RAW_GRIB_BODY,
    index_status: int = 200,
    object_status: int = 200,
    index_url_override: str | None = None,
    tamper_object_sha: str | None = None,
):
    def transport(url: str) -> tuple[dict[str, object], bytes]:
        if url == (index_url_override or aws_index_url(DAY)):
            return (
                {
                    "url": url,
                    "status": index_status,
                    "classification": "SUCCESS" if index_status == 200 else "FAILURE",
                    "body_sha256": hashlib.sha256(index_body).hexdigest(),
                },
                index_body,
            )
        return (
            {
                "url": url,
                "status": object_status,
                "classification": "SUCCESS" if object_status == 200 else "FAILURE",
                "body_sha256": tamper_object_sha or hashlib.sha256(object_body).hexdigest(),
            },
            object_body,
        )

    return transport


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_acquire_happy_path() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is True
    assert result.object_name == GOOD_NAME
    assert result.host == AWS_HOST
    assert result.raw_sha256 == hashlib.sha256(RAW_GRIB_BODY).hexdigest()
    assert result.object_url == object_url(DAY, GOOD_NAME)


def test_validate_happy_path_round_trips() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    validation = validate_acquired_forecast_source(
        result.to_json(), expected_day=DAY, now=NOW, maximum_age=timedelta(hours=6)
    )
    assert validation.succeeded is True
    assert validation.object_name == GOOD_NAME


def test_evidence_never_claims_semantic_fields() -> None:
    """This module must never itself claim DAILY_MAX/KMDW/date/midpoint/model-identity -- only
    provenance fields belong on the evidence artifact."""
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    payload = result.to_json()
    forbidden_keys = {
        "measurement",
        "daily_max",
        "kmdw_correct",
        "local_target_date",
        "midpoint",
        "model_identity",
        "reference_time",
    }
    assert not (set(payload) & forbidden_keys)


# ---------------------------------------------------------------------------
# Selection-rule rejections
# ---------------------------------------------------------------------------


def test_select_rejects_zero_candidates() -> None:
    with pytest.raises(SourceAcquisitionError, match="found 0"):
        select_candidate_object_name(_index_xml(()))


def test_select_rejects_ambiguous_multiple_candidates() -> None:
    with pytest.raises(SourceAcquisitionError, match="found 2"):
        select_candidate_object_name(
            _index_xml(("YGUZ98_KWBN_2026082002", "YGUZ98_KWBN_2026082002b"))
        )


def test_select_rejects_wrong_hour_suffix() -> None:
    # hour suffix "04" instead of the reviewed "02" for this MAXT family
    with pytest.raises(SourceAcquisitionError, match="found 0"):
        select_candidate_object_name(_index_xml(("YGUZ98_KWBN_2026082004",)))


def test_acquire_rejects_ambiguous_source_selection() -> None:
    transport = _fake_transport(
        index_body=_index_xml(("YGUZ98_KWBN_2026082002", "YGUZ98_KWBN_2026082002b"))
    )
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is False
    assert result.classification == "AMBIGUOUS_SOURCE_SELECTION"


# ---------------------------------------------------------------------------
# Transport/envelope rejections
# ---------------------------------------------------------------------------


def test_acquire_rejects_wrong_host() -> None:
    with pytest.raises(SourceAcquisitionError, match="allowlisted"):
        from services.forecasting.weather_current_cycle_acquisition import _validate_source_url

        _validate_source_url("https://not-aws.example.com/wmo/maxt/2026/08/20/")


def test_acquire_rejects_non_https() -> None:
    with pytest.raises(SourceAcquisitionError, match="allowlisted"):
        from services.forecasting.weather_current_cycle_acquisition import _validate_source_url

        _validate_source_url(f"http://{AWS_HOST}/wmo/maxt/2026/08/20/")


def test_acquire_rejects_oversized_object() -> None:
    huge = b"GRIB" + b"\x00" * (5_000_001)
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)), object_body=huge)
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is False
    assert result.classification == "OVERSIZED_BODY"


def test_acquire_rejects_empty_object() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)), object_body=b"")
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_ENVELOPE"


def test_acquire_rejects_non_grib_object() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)), object_body=b"NOTGRIB!!!")
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is False
    assert "GRIB magic" in (result.reason or "")


def test_acquire_rejects_object_hash_mismatch() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)), tamper_object_sha="0" * 64)
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_ENVELOPE"


def test_acquire_rejects_index_http_failure() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)), index_status=500)
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is False
    assert result.classification == "HTTP_OR_NETWORK_FAILURE"


def test_acquire_rejects_malformed_index_xml() -> None:
    transport = _fake_transport(index_body=b"not xml at all")
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_ENVELOPE"


# ---------------------------------------------------------------------------
# Validation-side rejections (tampering an already-acquired, serialized payload)
# ---------------------------------------------------------------------------


def test_validate_rejects_future_timestamp() -> None:
    future = NOW + timedelta(hours=1)
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: future)
    validation = validate_acquired_forecast_source(
        result.to_json(), expected_day=DAY, now=NOW, maximum_age=timedelta(hours=6)
    )
    assert validation.succeeded is False
    assert "future" in (validation.reason or "").lower()


def test_validate_rejects_stale_cycle() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    far_future_now = NOW + timedelta(hours=12)
    validation = validate_acquired_forecast_source(
        result.to_json(), expected_day=DAY, now=far_future_now, maximum_age=timedelta(hours=6)
    )
    assert validation.succeeded is False
    assert validation.classification == "ACQUISITION_EVIDENCE_STALE"


def test_validate_rejects_wrong_family() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    tampered = dict(result.to_json())
    tampered["family_identity"] = "SOME_OTHER_FAMILY"
    validation = validate_acquired_forecast_source(
        tampered, expected_day=DAY, now=NOW, maximum_age=timedelta(hours=6)
    )
    assert validation.succeeded is False
    assert validation.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_validate_rejects_tampered_selection_identity() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    tampered = dict(result.to_json())
    tampered["object_name"] = "YGUZ98_KWBN_2026082099"  # identity no longer matches
    validation = validate_acquired_forecast_source(
        tampered, expected_day=DAY, now=NOW, maximum_age=timedelta(hours=6)
    )
    assert validation.succeeded is False


def test_validate_rejects_wrong_day_binding() -> None:
    transport = _fake_transport(index_body=_index_xml((GOOD_NAME,)))
    result = acquire_current_cycle_raw_grib(DAY, transport=transport, clock=lambda: NOW)
    other_day = date(2026, 8, 21)
    validation = validate_acquired_forecast_source(
        result.to_json(), expected_day=other_day, now=NOW, maximum_age=timedelta(hours=6)
    )
    assert validation.succeeded is False
    assert validation.classification == "SOURCE_AUTHORITY_MISMATCH"


# ---------------------------------------------------------------------------
# Zero credentials/Kalshi/subprocess capability
# ---------------------------------------------------------------------------

_FORBIDDEN_NAMES = {
    "KalshiAccountClient",
    "RequestSigner",
    "AuthorizationStore",
    "CanaryStore",
    "ProtectedWriteCredentialStore",
}


def test_module_has_no_kalshi_credential_or_subprocess_capability() -> None:
    source = Path("services/forecasting/weather_current_cycle_acquisition.py").read_text()
    tree = ast.parse(source)
    names: set[str] = set()
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    assert not (names & _FORBIDDEN_NAMES), names & _FORBIDDEN_NAMES
    assert not any("kalshi" in module.lower() for module in imported_modules), imported_modules
    forbidden_modules = {"subprocess", "http.client", "urllib.request", "requests"}
    assert not (imported_modules & forbidden_modules), imported_modules & forbidden_modules
