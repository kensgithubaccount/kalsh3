from copy import deepcopy

import pytest

from services.contract_intelligence.domain import Approval, StructuredRulesParser, reconcile
from services.market_universe.domain import UniverseValidationError


def fixture() -> dict[str, object]:
    return {
        "ticker": "M",
        "title": "Will temperature exceed 70 F?",
        "rules_primary": "YES if final NOAA daily high is greater than 70 F.",
        "rules_secondary": "Final published value.",
        "yes_meaning": "Final NOAA high > 70 F",
        "no_meaning": "Final NOAA high <= 70 F",
        "settlement_authority": "NOAA",
        "settlement_sources": [
            {
                "name": "NOAA climate report",
                "url": "https://weather.gov",
                "authority": "NOAA",
                "primary": True,
            }
        ],
        "deadline": "2026-08-11T23:59:00-04:00",
        "timezone": "America/New_York",
        "comparator": ">",
        "threshold": "70.0",
        "unit": "F",
        "rounding": "as published",
        "revision_policy": "first final report",
        "recount_policy": None,
        "cancellation_policy": "market rules",
        "postponement_policy": "market rules",
        "early_close_condition": None,
        "exceptions": [],
    }


def test_structured_semantics_exact_and_deterministic() -> None:
    parser = StructuredRulesParser()
    one = parser.parse(fixture())
    two = parser.parse(fixture())
    assert (
        one.semantics_hash == two.semantics_hash
        and str(one.threshold) == "70.0"
        and reconcile([one, two]).approval == Approval.APPROVED
    )


def test_ambiguity_contradiction_and_rules_change_fail_closed() -> None:
    parser = StructuredRulesParser()
    old = parser.parse(fixture())
    changed = deepcopy(fixture())
    changed["rules_primary"] = "Changed defining rule"
    new = parser.parse(changed)
    assert (
        reconcile([]).approval == Approval.AMBIGUOUS
        and reconcile([old, new]).approval == Approval.CONTRADICTED
        and reconcile([new], old).approval == Approval.RULES_CHANGED
    )


def test_missing_authority_source_deadline_and_multiple_primary_rejected() -> None:
    parser = StructuredRulesParser()
    for key in ("yes_meaning", "settlement_authority", "deadline"):
        raw = fixture()
        raw[key] = None
        with pytest.raises(UniverseValidationError):
            parser.parse(raw)
    raw = fixture()
    raw["settlement_sources"] = [
        {"name": "a", "authority": "a", "primary": True},
        {"name": "b", "authority": "b", "primary": True},
    ]
    with pytest.raises(UniverseValidationError):
        parser.parse(raw)
