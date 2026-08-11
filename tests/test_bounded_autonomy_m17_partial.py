from services.bounded_autonomy.domain import AutonomyEvidence, AutonomyState


def _evidence(value: bool) -> AutonomyEvidence:
    return AutonomyEvidence(**{name: value for name in AutonomyEvidence.__dataclass_fields__})


def test_m17_autonomy_stays_off_even_with_all_fixture_evidence() -> None:
    evidence = _evidence(True)
    assert evidence.state() == AutonomyState.OFF
    assert tuple(AutonomyState) == (AutonomyState.OFF,)


def test_missing_evidence_is_explicit_and_fail_closed() -> None:
    evidence = _evidence(False)
    assert len(evidence.missing()) == len(AutonomyEvidence.__dataclass_fields__)
    assert evidence.state() == AutonomyState.OFF
