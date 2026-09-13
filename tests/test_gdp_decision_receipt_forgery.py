from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_d1_g2_p1_one_decision import FixtureSource, _clock, _gdpnow, _response

from services.forward_reality.trial_ledger import TrialLedger, TrialStatus
from services.production_gdp_strategy import one_decision as od
from services.production_gdp_strategy.gdp_persistence import (
    RESEARCH_STORAGE_ROOT_ENV,
    DecisionArchive,
    open_default_research_storage,
    register_gdp_attempt,
)


def _receipt() -> od.DecisionReceipt:
    issue, _restore, _archive_restore, _validate = od._make_decision_issuer()
    values = {
        "trial_id": "fixture-trial",
        "underlying_event_id": "fixture-event",
        "decision_id": "fixture-decision",
        "policy_version": od.POLICY_VERSION,
        "policy_hash": od._policy_hash(),
        "entry_rule_version": od.ENTRY_RULE_VERSION,
        "entry_rule_hash": od.stable_hash((od.ENTRY_RULE_VERSION, str(od.GATE))),
        "tradability_protocol_version": od.TRADABILITY_PROTOCOL_VERSION,
        "tradability_protocol_hash": od.stable_hash(
            (od.TRADABILITY_PROTOCOL_VERSION, od.MAX_STATUS_BOOK_STATUS_WINDOW_MS)
        ),
        "decision_timestamp": datetime.now(UTC),
        "pipeline_start_timestamp": datetime.now(UTC),
        "pipeline_completion_timestamp": datetime.now(UTC),
        "schedule_id": "UNAVAILABLE",
        "schedule_gate_passed": False,
        "gdpnow_acquisition_id": "UNAVAILABLE",
        "gdpnow_vintage_id": "UNAVAILABLE",
        "market_before_id": "UNAVAILABLE",
        "orderbook_id": "UNAVAILABLE",
        "market_after_id": "UNAVAILABLE",
        "fee_id": "UNAVAILABLE",
        "selected_market_ticker": "UNAVAILABLE",
        "selected_threshold": od.ZERO,
        "signal_side": None,
        "entry_price": None,
        "quantity": od.ONE,
        "depth_at_entry": od.ZERO,
        "entry_fee": None,
        "all_in_debit": None,
        "entry_gate_value": od.GATE,
        "entry_gate_passed": False,
        "classification": od.DecisionClass.EVIDENCE_INCOMPLETE,
        "observation_window_elapsed_ms": 0,
        "whole_window_inside_open_close": False,
        "whole_window_before_cutoff": False,
        "active_before": False,
        "active_after": False,
        "research_only": True,
        "production_influence": od.ZERO,
    }
    return issue(values, None)


def test_canonical_generic_issuer_and_restore_are_not_module_attributes() -> None:
    assert not callable(getattr(od, "_issue_live_decision", None))
    assert not callable(getattr(od, "_restore_decision_from_authenticated_record", None))
    assert not hasattr(od, "_restore_authenticated_decision")
    assert not callable(getattr(od, "_incomplete_receipt", None))
    assert not callable(getattr(od, "_restore_archived_decision", None))


def test_direct_and_copied_receipts_fail_closed() -> None:
    with pytest.raises(od.DecisionError):
        od.DecisionReceipt()
    original = _receipt()
    forged = object.__new__(od.DecisionReceipt)
    for field in od.fields(od.DecisionReceipt):
        object.__setattr__(forged, field.name, getattr(original, field.name))
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(forged)


def test_payload_hash_and_attacker_key_mac_cannot_bless_receipt() -> None:
    original = _receipt()
    forged = object.__new__(od.DecisionReceipt)
    for field in od.fields(od.DecisionReceipt):
        object.__setattr__(forged, field.name, getattr(original, field.name))
    object.__setattr__(forged, "payload_hash", original.payload_hash)
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(forged)
    assert not hasattr(od, "_restore_decision_from_authenticated_record")


def test_factory_receipts_are_isolated_from_canonical_validation() -> None:
    issue, _restore, _archive_restore, _isolated_validate = od._make_decision_issuer()
    original = _receipt()
    values = {
        field.name: getattr(original, field.name)
        for field in od.fields(od.DecisionReceipt)
        if field.name not in {"payload_hash", "bundle"}
    }
    receipt = issue(values, None)
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(receipt)


def test_attacker_created_archive_cannot_replay_without_ledger_authority(tmp_path) -> None:
    archive = DecisionArchive(tmp_path / "decisions")
    with pytest.raises(od.DecisionError):
        od.replay_gdp_decision(object(), archive, "attacker-trial")


def test_attacker_created_ledger_archive_pair_without_public_record_cannot_replay(tmp_path) -> None:
    ledger = TrialLedger(tmp_path / "ledger.sqlite")
    trial = register_gdp_attempt(ledger)
    archive = DecisionArchive(tmp_path / "decisions")
    with pytest.raises(od.DecisionError):
        od.replay_gdp_decision(ledger, archive, trial.trial_id)


def _no_signal_fixture_decision() -> od.DecisionReceipt:
    """Build a fixture TRADE_NO decision through the real evaluator, not a hand-built receipt."""
    evidence, vintage = _gdpnow()

    class NoSignalSource:
        def schedule(self) -> od._TransportResponse:
            return _response(
                "/reviewed/d1-g2/schedule",
                {
                    "schedule": {
                        "series_ticker": "KXGDP",
                        "event_ticker": "KXGDP-26SEP03",
                        "market_ticker": "KXGDP-26SEP03-T4.5",
                        "target_quarter": "2026-Q3",
                        "metric": (
                            "US real GDP quarter-over-quarter growth, seasonally "
                            "adjusted annual rate"
                        ),
                        "settlement_edition": "BEA Advance Estimate",
                        "timezone": "America/New_York",
                        "bea_release_local": "2026-09-03T08:30:00",
                        "market_open_local": "2026-09-03T07:00:00",
                        "market_close_local": "2026-09-03T08:29:00",
                        "listed_thresholds": ["10.0"],
                    }
                },
            )

        def market(self, ticker: str) -> od._TransportResponse:
            assert ticker == "KXGDP-26SEP03-T4.5"
            return _response(
                f"/trade-api/v2/markets/{ticker}",
                {
                    "market": {
                        "ticker": ticker,
                        "event_ticker": "KXGDP-26SEP03",
                        "market_type": "binary",
                        "status": "active",
                        "rules_primary": (
                            "US real GDP seasonally adjusted annualized BEA Advance "
                            "Estimate more than threshold"
                        ),
                        "price_level_structure": "deci_cent",
                        "open_time": "2026-09-03T11:00:00+00:00",
                        "close_time": "2026-09-03T12:29:00+00:00",
                        "threshold": "10.0",
                        "volume_fp": "0",
                        "open_interest_fp": "0",
                    }
                },
            )

        def orderbook(self, ticker: str) -> od._TransportResponse:
            assert ticker == "KXGDP-26SEP03-T4.5"
            return _response(
                "/trade-api/v2/markets/orderbooks?tickers=KXGDP-26SEP03-T4.5",
                {
                    "orderbooks": [
                        {
                            "ticker": "KXGDP-26SEP03-T4.5",
                            "orderbook_fp": {
                                "yes_dollars": [["0.55", "2.00"]],
                                "no_dollars": [["0.60", "2.00"]],
                            },
                        }
                    ]
                },
            )

        def fee(self) -> od._TransportResponse:
            return _response(
                "/reviewed/d1-g2/fee",
                {
                    "fee": {
                        "schedule_id": "kalshi-fee-test-v1",
                        "effective_at": "2026-07-07T00:00:00+00:00",
                        "series_ticker": "KXGDP",
                        "fee_type": "quadratic",
                        "fee_multiplier": "1",
                        "regime": "MARKETABLE_TAKER",
                        "formula": "0.07 * price * (1-price) * quantity * multiplier",
                        "price_units": "USD_PER_CONTRACT",
                        "quantity_convention": "CONTRACTS",
                        "rounding": "CEILING_0.0001",
                        "decimal_precision": "EXACT_DECIMAL",
                        "override_precedence": "market>event>series>schedule",
                    }
                },
            )

    return od._evaluate_fixture_decision(
        source=NoSignalSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )


def test_fixture_trade_yes_receipt_fails_canonical_validation() -> None:
    """A0.5/D1-G2 boundary: a positive fixture decision must never gain canonical authority."""
    evidence, vintage = _gdpnow()
    fixture = od._evaluate_fixture_decision(
        source=FixtureSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    assert fixture.classification is od.DecisionClass.TRADE_YES
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(fixture)
    # The isolated fixture validator still recognizes it: this is a genuinely
    # issued receipt, just never a canonical one.
    od._validate_fixture_decision_receipt(fixture)


def test_fixture_trade_no_receipt_fails_canonical_validation() -> None:
    fixture = _no_signal_fixture_decision()
    assert fixture.classification is od.DecisionClass.TRADE_NO
    with pytest.raises(od.DecisionError):
        od.validate_decision_receipt(fixture)
    od._validate_fixture_decision_receipt(fixture)


def test_fixture_trade_receipt_cannot_be_archived_as_canonical(tmp_path: Path) -> None:
    """No canonical persistence/archive operation may accept a fixture receipt."""
    evidence, vintage = _gdpnow()
    fixture = od._evaluate_fixture_decision(
        source=FixtureSource(), gdpnow=evidence, vintage=vintage, clock=_clock()
    )
    archive = DecisionArchive(tmp_path / "decisions")
    with pytest.raises(od.DecisionError):
        archive.append(fixture, trial_id="fixture-trial", underlying_event_id="fixture-event")


def _genuine_ledger_and_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TrialLedger, DecisionArchive, str]:
    """Produce a real public persisted attempt through the canonical zero-arg entrypoint."""
    monkeypatch.setenv(RESEARCH_STORAGE_ROOT_ENV, str(tmp_path / "gdp-research"))
    result = od.run_one_research_decision()
    ledger, archive = open_default_research_storage()
    return ledger, archive, str(result.trial_id)


def test_duck_typed_fake_ledger_matching_trial_shape_cannot_replay_genuine_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller-authored ledger stand-in must not be able to impersonate TrialLedger authority,

    even when its `get()` returns an object that copies every field the replay
    boundary checks (candidate family, model/feature identity, evaluation plan
    identity, reason, underlying event, research_only, production_influence,
    COMPLETED status) from the genuine trial.
    """
    ledger, archive, trial_id = _genuine_ledger_and_archive(tmp_path, monkeypatch)
    genuine_trial = ledger.get(trial_id)

    class AttackerTrial:
        trial_id = genuine_trial.trial_id
        candidate_family = genuine_trial.candidate_family
        model_identity = genuine_trial.model_identity
        feature_specification_identity = genuine_trial.feature_specification_identity
        definition = genuine_trial.definition
        reason = genuine_trial.reason
        underlying_event_id = genuine_trial.underlying_event_id
        research_only = True
        production_influence = 0
        status = TrialStatus.COMPLETED

    class FakeLedger:
        def get(self, requested_trial_id: str) -> object:
            assert requested_trial_id == trial_id
            return AttackerTrial()

    with pytest.raises(od.DecisionError):
        od.replay_gdp_decision(FakeLedger(), archive, trial_id)


def test_arbitrary_object_with_get_method_cannot_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger, archive, trial_id = _genuine_ledger_and_archive(tmp_path, monkeypatch)

    class ArbitraryGetter:
        def get(self, requested_trial_id: str) -> object:
            del requested_trial_id
            return object()

    with pytest.raises(od.DecisionError):
        od.replay_gdp_decision(ArbitraryGetter(), archive, trial_id)


def test_trial_ledger_subclass_cannot_replay_as_exact_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The chosen authority contract is the exact TrialLedger type, not any subclass/proxy."""
    ledger, archive, trial_id = _genuine_ledger_and_archive(tmp_path, monkeypatch)

    class SubLedger(TrialLedger):
        pass

    sub_ledger = SubLedger(ledger._path)
    # The subclass is behaviorally identical and returns the real, genuine trial.
    assert sub_ledger.get(trial_id).trial_id == trial_id
    with pytest.raises(od.DecisionError):
        od.replay_gdp_decision(sub_ledger, archive, trial_id)


def test_genuine_ledger_and_archive_replay_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, archive, trial_id = _genuine_ledger_and_archive(tmp_path, monkeypatch)
    result = od.replay_gdp_decision(ledger, archive, trial_id)
    assert result.trial_id == trial_id
    assert result.classification is od.DecisionClass.EVIDENCE_INCOMPLETE
    assert result.research_only is True
    assert result.production_influence == od.ZERO
