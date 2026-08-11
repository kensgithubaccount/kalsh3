from services.bounded_autonomy.domain import AutonomyEvidence, AutonomyState


def test_m17_autonomy_stays_off_even_with_all_fixture_evidence() -> None:
    evidence = AutonomyEvidence(True, True, True, True, True, True)
    assert evidence.state() == AutonomyState.OFF
    assert tuple(AutonomyState) == (AutonomyState.OFF,)


def test_missing_evidence_is_explicit_and_fail_closed() -> None:
    evidence = AutonomyEvidence(False, False, False, False, False, False)
    assert len(evidence.missing()) == 6
    assert evidence.state() == AutonomyState.OFF
