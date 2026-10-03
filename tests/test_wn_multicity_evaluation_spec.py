from __future__ import annotations

from datetime import date

import pytest

from services.forecasting.wn_multicity_evaluation_spec import (
    CITY_DAYS_INDEPENDENT,
    EXPECTED_CITY_IDS,
    FROZEN_PROTOCOL_SHA256,
    PRE_FIRST_EVENT_AMENDMENT_SHA256,
    PRIMARY_INDEPENDENCE_UNIT,
    PROSPECTIVE_END,
    PROSPECTIVE_START,
    REVIEWED_IMAGE_DIGEST,
    CaptureState,
    MulticityEvaluationSpecError,
    audit_inventory,
    classify_city_day_objects,
    expected_city_days,
    expected_target_dates,
)


def test_frozen_identity_constants_match_reviewed_experiment() -> None:
    assert FROZEN_PROTOCOL_SHA256 == (
        "56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346"
    )
    assert PRE_FIRST_EVENT_AMENDMENT_SHA256 == (
        "b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000"
    )
    assert REVIEWED_IMAGE_DIGEST == (
        "sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08"
    )


def test_exact_frozen_roster_is_twelve_dates_and_sixty_city_days() -> None:
    dates = expected_target_dates()
    keys = expected_city_days()
    assert dates[0] == PROSPECTIVE_START
    assert dates[-1] == PROSPECTIVE_END
    assert len(dates) == 12
    assert len(keys) == 60
    assert len(EXPECTED_CITY_IDS) == 5


def test_primary_independence_is_date_cluster_not_city_day() -> None:
    assert CITY_DAYS_INDEPENDENT is False
    assert PRIMARY_INDEPENDENCE_UNIT == "TARGET_DATE_CLUSTER"


@pytest.mark.parametrize(
    ("objects", "expected"),
    [
        (["x/packet.json"], CaptureState.CAPTURED),
        (["x/failure.json"], CaptureState.FAILED),
        (["x/start.json"], CaptureState.MISSING),
        (["x/packet.json", "x/failure.json"], CaptureState.CONFLICT),
    ],
)
def test_city_day_structural_classification(objects: list[str], expected: CaptureState) -> None:
    assert classify_city_day_objects(objects) is expected


def test_duplicate_object_path_fails_closed() -> None:
    with pytest.raises(MulticityEvaluationSpecError, match="duplicate object path"):
        classify_city_day_objects(["x/packet.json", "x/packet.json"])


def test_october_3_full_capture_is_one_complete_independence_cluster() -> None:
    paths = [
        f"gs://bucket/root/city_days/{city}/20261003/packet.json" for city in EXPECTED_CITY_IDS
    ]
    report = audit_inventory(paths, as_of=date(2026, 10, 3))
    assert report.expected_city_days == 5
    assert report.captured_city_days == 5
    assert report.complete_date_clusters == 1
    assert report.expected_date_clusters == 1
    assert report.operationally_clean is True


def test_missing_one_city_breaks_cluster_completeness_without_inventing_failure() -> None:
    paths = [
        f"gs://bucket/root/city_days/{city}/20261003/packet.json" for city in EXPECTED_CITY_IDS[:-1]
    ]
    report = audit_inventory(paths, as_of=date(2026, 10, 3))
    assert report.captured_city_days == 4
    assert report.missing_city_days == 1
    assert report.failed_city_days == 0
    assert report.complete_date_clusters == 0
    assert report.operationally_clean is False


def test_future_or_unknown_city_path_is_reported_not_silently_adopted() -> None:
    paths = [
        "gs://bucket/root/city_days/chicago/20261003/packet.json",
        "gs://bucket/root/city_days/boston/20261015/packet.json",
    ]
    report = audit_inventory(paths, as_of=date(2026, 10, 3))
    assert len(report.unexpected_paths) == 2
    assert report.operationally_clean is False


def test_before_start_has_zero_expected_observations() -> None:
    report = audit_inventory([], as_of=date(2026, 10, 2))
    assert report.expected_city_days == 0
    assert report.expected_date_clusters == 0
    assert report.operationally_clean is True
