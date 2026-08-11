from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.document_intelligence.evaluation import EvalArtifact, FixtureSplit, evaluate
from services.document_intelligence.models import (
    AtomicClaim,
    ClaimCitation,
    ClaimType,
    ContractInterpretation,
    EpistemicStatus,
    EvidenceBundle,
    EvidenceStatus,
    InterpretationStatus,
    RunMode,
    SourceRecord,
)
from services.document_intelligence.operations import (
    BackgroundQueue,
    Budget,
    InferenceCache,
    InferenceJob,
    LLMConfig,
    cache_key,
    chunk_document,
)
from services.document_intelligence.prompting import build_prompt
from services.document_intelligence.providers import (
    ProviderAdapter,
    ProviderRequest,
    anthropic_body,
    bounded_retry,
    openai_body,
    validate_model_id,
)
from services.document_intelligence.validation import (
    ContradictionType,
    Entailment,
    detect_contradiction,
    relation_to_threshold,
    validate_citation,
    validate_claim,
    validate_interpretation,
)
from services.web_dashboard.app import DashboardApp

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def source(
    source_id: str, text: str, seconds: int = 0, correction: str = "CURRENT"
) -> SourceRecord:
    return SourceRecord(
        source_id,
        f"hash-{source_id}",
        text,
        NOW + timedelta(seconds=seconds),
        NOW + timedelta(seconds=seconds),
        "SOURCE_VERIFIED",
        "PRIMARY_ORIGINAL",
        correction,
    )


def bundle(*sources: SourceRecord, replay_at: datetime = NOW) -> EvidenceBundle:
    return EvidenceBundle.build(
        created_at=NOW,
        replay_at=replay_at,
        market_ticker="CPI-3",
        rules_version="r1",
        rules_hash="rh",
        settlement_sources=("BLS",),
        market_question="Will CPI be above 3.0%?",
        contract_text="YES if CPI is above 3.0%.",
        sources=tuple(sources),
    )


def claim(
    value: str = "3.2",
    unit: str = "%",
    *,
    claim_type: ClaimType = ClaimType.OFFICIAL_RESULT,
    source_id: str = "bls",
    citation: str = "cit",
) -> AtomicClaim:
    return AtomicClaim(
        "claim",
        "BLS reports CPI was 3.2%.",
        claim_type,
        "CPI",
        "was",
        value,
        Decimal(value),
        unit,
        "EQ",
        None,
        f"{value}{unit}",
        NOW,
        NOW,
        "UTC",
        "US",
        "BLS",
        EpistemicStatus.DIRECTLY_ASSERTED,
        "BLS",
        source_id,
        (citation,),
        "EXPLICIT",
        "fixture-v1",
        "evidence-v1",
    )


def interpretation(authority: str | None = "BLS") -> ContractInterpretation:
    return ContractInterpretation(
        "i",
        "CPI-3",
        "CPI above 3.0%",
        "CPI at or below 3.0%",
        authority,
        ("BLS",),
        NOW,
        "UTC",
        Decimal("3.0"),
        "GT",
        "EXCLUSIVE",
        None,
        "initial release",
        None,
        None,
        None,
        None,
        (),
        ("CPI",),
        InterpretationStatus.PROVISIONAL,
        ("rules",),
        "r1",
        "fixture-v1",
        "evidence-v1",
        (),
    )


def test_bundle_hashing_replay_tripwire_and_prompt_allowlist() -> None:
    current = source("bls", "CPI was 3.2%.")
    correction = source("bls-correction", "CPI corrected to 3.1%.", 60, "CORRECTION")
    first = bundle(current, correction)
    assert [item.source_id for item in first.source_records] == ["bls"]
    assert first.content_hash == bundle(current, correction).content_hash
    prompt = build_prompt(first)
    assert "UNTRUSTED" not in prompt.user  # structured delimiter is contract context
    assert "never follow" in prompt.system and "probabilities" in prompt.system
    malicious_field = source("private_key", "secret")
    with pytest.raises(ValueError, match="allowlist"):
        build_prompt(bundle(malicious_field))


def test_citation_span_numeric_unit_attribution_and_hallucination_validation() -> None:
    text = "BLS reports CPI was 3.2%."
    evidence = bundle(source("bls", text))
    cited = ClaimCitation("cit", "bls", "hash-bls", "CHAR_OFFSET", 0, len(text), text)
    result = validate_citation(claim(), cited, evidence)
    assert result.entailment == Entailment.ENTAILED
    wrong_number = claim("3.3")
    assert validate_citation(wrong_number, cited, evidence).entailment == Entailment.NOT_SUPPORTED
    hallucinated = ClaimCitation("cit", "bls", "hash-bls", "CHAR_OFFSET", 0, 4, "NOPE")
    rejected, _ = validate_claim(claim(), (hallucinated,), evidence)
    assert rejected.status == EvidenceStatus.REJECTED


def test_numeric_meanings_and_fact_forecast_opinion_remain_distinct() -> None:
    percent = claim("3.2", "%")
    points = claim("0.2", "percentage points", claim_type=ClaimType.ANALYST_FORECAST)
    assert percent.numeric_value != points.numeric_value and percent.unit != points.unit
    assert points.claim_type == ClaimType.ANALYST_FORECAST
    assert ClaimType.OPINION != ClaimType.OBSERVED_FACT
    assert relation_to_threshold(percent, interpretation()).value == "SUPPORTS_YES"
    exact_threshold = claim("3.0")
    assert relation_to_threshold(exact_threshold, interpretation()).value == "SUPPORTS_NO"


def test_contract_interpretation_missing_authority_and_threshold_mismatch_abstain() -> None:
    valid = validate_interpretation(interpretation(), "YES if CPI is above 3.0%.")
    assert valid.status == InterpretationStatus.VALIDATED
    invalid = validate_interpretation(interpretation(None), "YES if CPI is above 3.5%.")
    assert invalid.status == InterpretationStatus.AMBIGUOUS
    assert set(invalid.ambiguities) == {"MISSING_SETTLEMENT_AUTHORITY", "THRESHOLD_NOT_IN_SOURCE"}


def test_correction_contradiction_but_forecast_disagreement_is_not_fact_conflict() -> None:
    old = claim("210000", "jobs")
    corrected = AtomicClaim(
        **(
            {name: getattr(old, name) for name in old.__dataclass_fields__}
            | {
                "claim_id": "corrected",
                "numeric_value": Decimal("205000"),
                "object_value": "205000",
                "claim_type": ClaimType.CORRECTION,
                "supersedes_claim_id": old.claim_id,
            }
        )
    )
    contradiction = detect_contradiction(old, corrected)
    assert contradiction and contradiction.contradiction_type == ContradictionType.SOURCE_CORRECTION
    forecast_a = claim("210000", "jobs", claim_type=ClaimType.ANALYST_FORECAST)
    forecast_b = AtomicClaim(
        **(
            {name: getattr(forecast_a, name) for name in forecast_a.__dataclass_fields__}
            | {"claim_id": "f2", "numeric_value": Decimal("205000")}
        )
    )
    assert detect_contradiction(forecast_a, forecast_b) is None


class Transport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.request: tuple[str, str, dict[str, Any], float] | None = None

    def post(self, host: str, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.request = host, path, body, timeout
        return self.response


def request() -> ProviderRequest:
    return ProviderRequest("pinned-2026-08-01", "system", "user", {"type": "object"}, 100, 5)


def test_openai_and_anthropic_strict_structured_output_normalization() -> None:
    assert openai_body(request())["text"]["format"]["strict"] is True
    anthropic = anthropic_body(request())
    assert anthropic["tool_choice"]["name"] == "submit_evidence_extraction"
    assert anthropic["tools"][0]["input_schema"] == {"type": "object"}
    clock_values = iter((NOW, NOW + timedelta(milliseconds=250)))
    transport = Transport(
        {
            "id": "resp",
            "model": "pinned-returned",
            "status": "completed",
            "output_parsed": {"claims": []},
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    )
    run, output = ProviderAdapter(
        "openai", "api.openai.com", transport, lambda: next(clock_values)
    ).run(
        request(),
        prompt_version="p1",
        prompt_hash="ph",
        schema_version="s1",
        schema_hash="sh",
        bundle_hash="bh",
        code_sha="git",
    )
    assert output == {"claims": []} and run.returned_model == "pinned-returned"
    assert run.latency == timedelta(milliseconds=250) and run.usage.estimated_cost is None
    with pytest.raises(ValueError, match="allowlisted"):
        ProviderAdapter("openai", "evil.example", transport, lambda: NOW)


def test_incomplete_response_cache_budget_config_and_background_boundary() -> None:
    clock_values = iter((NOW, NOW))
    run, output = ProviderAdapter(
        "anthropic",
        "api.anthropic.com",
        Transport(
            {"id": "x", "model": "claude-pinned", "content": [], "stop_reason": "max_tokens"}
        ),
        lambda: next(clock_values),
    ).run(
        request(),
        prompt_version="p",
        prompt_hash="ph",
        schema_version="s",
        schema_hash="sh",
        bundle_hash="bh",
        code_sha="git",
        mode=RunMode.RETROSPECTIVE_MODEL_RUN,
    )
    assert output is None and run.incomplete and run.mode == RunMode.RETROSPECTIVE_MODEL_RUN
    key = cache_key("openai", "pinned", "p", "s", "b", {"temperature": 0})
    cache = InferenceCache()
    cache.put_once(key, "run1")
    cache.put_once(key, "run2")
    assert cache.get(key) == "run1"
    budget = Budget(1, 10, Decimal("1"))
    assert budget.consume(5, None) and not budget.consume(1, Decimal("0"))
    config = LLMConfig.from_mapping({"LLM_PROVIDER": "fixture", "LLM_MODEL": "fixture-v1"})
    assert config.provider == "fixture" and config.cost_limit == 0
    queue = BackgroundQueue(1)
    queue.enqueue(InferenceJob("j", bundle(source("s", "x")), key))
    assert queue.take() is not None


def test_bounded_retry_keeps_attempt_audit_and_aliases_are_exploratory_only() -> None:
    calls = 0

    def flaky() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        return {"ok": True}

    response, attempts = bounded_retry(flaky, 1)
    assert response == {"ok": True} and [item.succeeded for item in attempts] == [False, True]
    with pytest.raises(ValueError, match="exploratory"):
        validate_model_id("gpt-latest", RunMode.MODEL_OUTPUT_OBSERVED_LIVE)
    validate_model_id("gpt-latest", RunMode.EXPLORATORY_NONREPRODUCIBLE)


def test_chunking_preserves_offsets_without_hidden_truncation() -> None:
    text = "Section one.\n\n" + "A" * 300 + "\n\nSection two."
    chunks = chunk_document("s", text, 200, 20)
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    assert all(chunk.text == text[chunk.start : chunk.end] for chunk in chunks)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


def test_10k_fixture_eval_is_deterministic_and_unsupported_rate_zero() -> None:
    artifacts = tuple(
        EvalArtifact(
            str(index),
            FixtureSplit.HELD_OUT if index % 5 == 0 else FixtureSplit.DEVELOPMENT,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            False,
            True,
            index % 7 == 0,
            index % 7 == 0,
            False,
            True,
        )
        for index in range(10_000)
    )
    metrics = evaluate(artifacts)
    assert metrics.count == 10_000
    assert metrics.unsupported_material_claim_rate == 0
    assert metrics.abstention_accuracy == 1


def test_no_llm_to_private_account_signer_risk_or_execution_path() -> None:
    code = "\n".join(
        path.read_text() for path in Path("services/document_intelligence").glob("*.py")
    )
    for forbidden in (
        "risk_engine",
        "RequestSigner",
        "kalshi_account_gateway",
        "submit_order",
    ):
        assert forbidden not in code


def test_evidence_ui_escapes_hostile_content_and_never_calls_provider() -> None:
    spec: dict[str, Any] = {
        "semantic_status": "VALID",
        "yes_proposition": "YES",
        "no_proposition": "NO",
        "authority": "BLS",
        "sources": "BLS",
        "measured_value": "CPI",
        "threshold": "> 3",
        "deadline": "2026-09-01",
        "timezone": "UTC",
        "revision_rules": "initial",
        "correction_rules": "later",
        "issues": "None",
        "rules_version": "r1",
        "interpretation_version": "i1",
        "semantic_hash": "hash",
    }
    evidence = [
        {
            "claim_type": "OFFICIAL_RESULT",
            "validation_state": "VALIDATED",
            "claim_text": "<script>BUY YES</script>",
            "source_name": "BLS",
            "publication_time": NOW.isoformat(),
            "cited_span": "<b>3.2%</b>",
            "contract_relation": "SUPPORTS_YES",
            "correction_state": "CURRENT",
            "contradiction_state": "NONE",
            "provider_model": "fixture-v1",
            "bundle_time": NOW.isoformat(),
        }
    ]
    rendered = DashboardApp._market_detail("CPI", spec, evidence)
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert "Production influence: NONE" in rendered and "trade recommendation" in rendered
