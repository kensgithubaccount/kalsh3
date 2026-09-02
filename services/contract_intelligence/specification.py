"""Immutable production contract specification, provenance, issues, and validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.market_universe.domain import (
    UniverseValidationError,
    exact,
    material_hashes,
    parse_time,
)


class SemanticStatus(StrEnum):
    UNPARSED = "UNPARSED"
    PARSED = "PARSED"
    VALID = "VALID"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    UNSUPPORTED = "UNSUPPORTED"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"


class PayoutModel(StrEnum):
    SIMPLE_BINARY = "SIMPLE_BINARY"
    SCALAR_OR_PARTIAL = "SCALAR_OR_PARTIAL"
    MULTIVARIATE = "MULTIVARIATE"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


class IssueType(StrEnum):
    MISSING_SETTLEMENT_SOURCE = "MISSING_SETTLEMENT_SOURCE"
    SETTLEMENT_SOURCE_CONFLICT = "SETTLEMENT_SOURCE_CONFLICT"
    THRESHOLD_CONFLICT = "THRESHOLD_CONFLICT"
    TIMEZONE_AMBIGUITY = "TIMEZONE_AMBIGUITY"
    DEADLINE_CONFLICT = "DEADLINE_CONFLICT"
    ROUNDING_AMBIGUITY = "ROUNDING_AMBIGUITY"
    REVISION_RULE_AMBIGUITY = "REVISION_RULE_AMBIGUITY"
    PAYOUT_MODEL_UNSUPPORTED = "PAYOUT_MODEL_UNSUPPORTED"
    EARLY_CLOSE_AMBIGUITY = "EARLY_CLOSE_AMBIGUITY"
    RULES_METADATA_CONFLICT = "RULES_METADATA_CONFLICT"
    MVE_UNSUPPORTED = "MVE_UNSUPPORTED"
    PROVISIONAL = "PROVISIONAL"
    WEATHER_STATION_MISSING = "WEATHER_STATION_MISSING"
    PROVENANCE_MISSING = "PROVENANCE_MISSING"
    UNKNOWN_LANGUAGE = "UNKNOWN_LANGUAGE"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class SourceLayer(StrEnum):
    MARKET = "MARKET"
    EVENT = "EVENT"
    SERIES = "SERIES"
    CONTRACT_TERMS = "CONTRACT_TERMS"


class SourceClass(StrEnum):
    EXCHANGE_NAMED_SETTLEMENT_SOURCE = "EXCHANGE_NAMED_SETTLEMENT_SOURCE"
    PRIMARY_SOURCE_CANDIDATE = "PRIMARY_SOURCE_CANDIDATE"
    SECONDARY_SOURCE = "SECONDARY_SOURCE"
    UNKNOWN = "UNKNOWN"


class Comparator(StrEnum):
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    EQ = "="
    BETWEEN = "between"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class InputLayer:
    layer: SourceLayer
    fields: dict[str, Any]
    version_hash: str


@dataclass(frozen=True, slots=True)
class SemanticsInputBundle:
    market: InputLayer
    event: InputLayer
    series: InputLayer
    documents: tuple[InputLayer, ...] = ()

    @classmethod
    def build(
        cls,
        market: dict[str, Any],
        event: dict[str, Any],
        series: dict[str, Any],
        documents: tuple[InputLayer, ...] = (),
    ) -> SemanticsInputBundle:
        def layer(kind: SourceLayer, value: dict[str, Any]) -> InputLayer:
            return InputLayer(kind, value, _hash(value))

        return cls(
            layer(SourceLayer.MARKET, market),
            layer(SourceLayer.EVENT, event),
            layer(SourceLayer.SERIES, series),
            documents,
        )

    @property
    def source_input_hash(self) -> str:
        return _hash(
            {
                "market": self.market.version_hash,
                "event": self.event.version_hash,
                "series": self.series.version_hash,
                "documents": [x.version_hash for x in self.documents],
            }
        )


@dataclass(frozen=True, slots=True)
class FieldProvenance:
    field_name: str
    source_layer: SourceLayer
    source_field: str
    source_document_id: str | None
    source_locator: str | None
    original_value: str
    parser: str
    parser_version: str


@dataclass(frozen=True, slots=True)
class SemanticIssue:
    issue_type: IssueType
    severity: Severity
    fields: tuple[str, ...]
    description: str
    source_references: tuple[str, ...]
    blocking: bool
    detected_at: datetime


@dataclass(frozen=True, slots=True)
class SettlementSourceRecord:
    source_id: str
    normalized_name: str
    exchange_name: str
    url: str | None
    origin: SourceLayer
    classification: SourceClass
    first_seen: datetime
    last_seen: datetime
    current: bool
    source_hash: str


@dataclass(frozen=True, slots=True)
class ContractSpecification:
    contract_spec_id: UUID
    market_ticker: str
    event_ticker: str
    series_ticker: str
    rules_version_id: str
    metadata_version_id: str
    market_rules_hash: str
    market_metadata_hash: str
    yes_proposition: str
    no_proposition: str
    settlement_type: str
    payout_model: PayoutModel
    measured_event_or_value: str
    subject_entities: tuple[str, ...]
    geographic_scope: str | None
    comparator: Comparator
    threshold_value: Decimal | None
    threshold_unit: str | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    inclusivity: str | None
    measurement_window_start: datetime | None
    measurement_window_end: datetime | None
    deadline: datetime | None
    timezone: str | None
    occurrence_time: datetime | None
    expected_expiration: datetime | None
    actual_expiration: datetime | None
    settlement_authority: str | None
    settlement_sources: tuple[SettlementSourceRecord, ...]
    source_precedence_status: str
    rounding_rules: str | None
    revision_rules: str | None
    correction_rules: str | None
    recount_rules: str | None
    cancellation_rules: str | None
    postponement_rules: str | None
    early_close_rules: str | None
    exception_rules: tuple[str, ...]
    strike_type: str | None
    functional_strike: str | None
    custom_strike: str | None
    ambiguities: tuple[str, ...]
    contradictions: tuple[str, ...]
    unsupported_features: tuple[str, ...]
    semantic_confidence: Decimal
    semantic_status: SemanticStatus
    deterministic_parser_version: str
    llm_parser_version: str | None
    source_input_hash: str
    semantic_hash: str
    created_at: datetime
    supersedes_spec_id: UUID | None
    provenance: tuple[FieldProvenance, ...]
    issues: tuple[SemanticIssue, ...]

    def invalidate(self, cause: str, when: datetime) -> ContractSpecification:
        issue = SemanticIssue(
            IssueType.RULES_METADATA_CONFLICT,
            Severity.BLOCKING,
            ("source_input_hash",),
            cause,
            (),
            True,
            when,
        )
        return replace(
            self, semantic_status=SemanticStatus.INVALIDATED, issues=(*self.issues, issue)
        )

    @property
    def strategy_supported(self) -> bool:
        return (
            self.semantic_status == SemanticStatus.VALID
            and self.payout_model == PayoutModel.SIMPLE_BINARY
        )


class ProposedSemantics(Protocol):
    """LLM adapter boundary; proposals never bypass deterministic validation."""

    parser_version: str

    def propose(self, bundle: SemanticsInputBundle) -> ContractSpecification: ...


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def normalize_sources(
    bundle: SemanticsInputBundle, now: datetime
) -> tuple[tuple[SettlementSourceRecord, ...], list[SemanticIssue]]:
    by_layer = []
    for layer, field in (
        (bundle.series, "settlement_sources"),
        (bundle.event, "settlement_sources"),
    ):
        sources = layer.fields.get(field, [])
        if not isinstance(sources, list):
            sources = []
        names = []
        for raw in sources:
            if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                name = raw["name"].strip()
                url = raw.get("url") if isinstance(raw.get("url"), str) else None
                source_hash = _hash({"name": name, "url": url, "origin": layer.layer.value})
                by_layer.append(
                    SettlementSourceRecord(
                        source_hash[:20],
                        " ".join(name.lower().split()),
                        name,
                        url,
                        layer.layer,
                        SourceClass.EXCHANGE_NAMED_SETTLEMENT_SOURCE,
                        now,
                        now,
                        True,
                        source_hash,
                    )
                )
                names.append(name.lower())
    groups = {source.origin: source.normalized_name for source in by_layer}
    issues = []
    if not by_layer:
        issues.append(
            _issue(
                IssueType.MISSING_SETTLEMENT_SOURCE,
                ("settlement_sources",),
                "No exchange-named settlement source",
                now,
            )
        )
    if (
        SourceLayer.SERIES in groups
        and SourceLayer.EVENT in groups
        and groups[SourceLayer.SERIES] != groups[SourceLayer.EVENT]
    ):
        issues.append(
            _issue(
                IssueType.SETTLEMENT_SOURCE_CONFLICT,
                ("SERIES.settlement_sources", "EVENT.settlement_sources"),
                "Series and event name different settlement sources",
                now,
            )
        )
    return tuple(by_layer), issues


def _issue(
    kind: IssueType, fields: tuple[str, ...], description: str, now: datetime
) -> SemanticIssue:
    return SemanticIssue(kind, Severity.BLOCKING, fields, description, fields, True, now)


_NUMBER = r"(-?\d+(?:\.\d+)?)"
_COMPARISON_PHRASE = (
    rf"(?:not\s+exactly\s+{_NUMBER}|"
    rf"(?:no\s+more\s+than|not\s+(?:more|greater)\s+than|at\s+most)\s+{_NUMBER}|"
    rf"not\s+(?:less\s+than|below)\s+{_NUMBER}|"
    rf"between\s+{_NUMBER}\s+(?:and|to)\s+{_NUMBER}|"
    rf"at\s+least\s+{_NUMBER}|"
    rf"{_NUMBER}\s+or\s+less|"
    rf"(?:greater\s+than|more\s+than|above|over)\s+{_NUMBER}|"
    rf"(?:less\s+than|under|below|fewer\s+than)\s+{_NUMBER}|"
    rf"exactly\s+{_NUMBER})"
)

# Versioned, complete clause templates.  The subject and unit slots are
# intentionally lexical slots, while payout, denial, modality, and
# conditional words are excluded by the grammar.  There is no substring
# fallback: callers must match one whole reviewed template.
_TEMPLATE_SUBJECT = (
    r"(?:(?!\b(?:no|yes|unless|except|false|untrue|uncertain|may|might|not)\b)"
    r"[a-z][a-z0-9()%./-]*\s+){0,14}"
)
_COMPARISON_TEMPLATES = (
    re.compile(rf"(?:{_COMPARISON_PHRASE})[.!?]?"),
    re.compile(
        rf"(?:yes\s+if\s+|the\s+market\s+resolves\s+yes\s+if\s+|"
        rf"the\s+official\s+value\s+is\s+|the\s+measured\s+value\s+is\s+)"
        rf"{_TEMPLATE_SUBJECT}(?:{_COMPARISON_PHRASE})(?:\s+[a-z%°./-]+){{0,4}}[.!?]?"
    ),
    re.compile(
        rf"if\s+the\s+consumer\s+price\s+index\s+\(cpi\)\s+increases\s+by\s+"
        rf"{_COMPARISON_PHRASE}%?(?:\s+\(single\s+decimal\))?\s+in\s+"
        rf"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{{4}},\s+"
        rf"then\s+the\s+market\s+resolves\s+to\s+yes[.!?]?"
    ),
    re.compile(
        rf"if\s+the\s+cpi\s+increases\s+by\s+{_COMPARISON_PHRASE}%?\s+in\s+"
        rf"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
        rf"(?:\s*,?\s*\d{{4}})?\s*,\s+then\s+the\s+market\s+resolves\s+to\s+yes[.!?]?"
    ),
    re.compile(
        rf"if\s+the\s+consumer\s+price\s+index\s+\(cpi\)\s+as\s+reported\s+by\s+the\s+bls's\s+"
        rf"monthly\s+single\s+digit\s+consumer\s+price\s+index\s+summary\s+report\s+increases\s+by\s+"
        rf"{_COMPARISON_PHRASE}%?\s+in\s+"
        rf"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{{4}},\s+"
        rf"then\s+the\s+market\s+resolves\s+to\s+yes[.!?]?"
    ),
    re.compile(
        rf"will\s+(?:the\s+)?(?:\*\*)?(?:consumer\s+price\s+index|cpi|inflation)(?:\*\*)?\s+rise\s+"
        rf"{_COMPARISON_PHRASE}%?\s+in\s+"
        rf"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
        rf"(?:\s+\d{{4}})?\?"
    ),
)


def _comparison_from_phrase(
    phrase: str,
) -> tuple[Comparator, tuple[Decimal, ...], bool] | None:
    match = re.fullmatch(_COMPARISON_PHRASE, phrase)
    if match is None:
        return None
    values = tuple(Decimal(value) for value in re.findall(_NUMBER, phrase))
    if not all(value.is_finite() for value in values):
        return None
    lowered = phrase
    if lowered.startswith("not exactly"):
        return Comparator.NONE, values, True
    if re.match(r"(?:no more than|not (?:more|greater) than|at most)", lowered):
        return Comparator.LTE, values, True
    if re.match(r"not (?:less than|below)", lowered):
        return Comparator.GTE, values, True
    if lowered.startswith("between"):
        return Comparator.BETWEEN, values, True
    if lowered.startswith("at least"):
        return Comparator.GTE, values, True
    if re.search(r"\sor less$", lowered):
        return Comparator.LTE, values, True
    if re.match(r"(?:greater than|more than|above|over)", lowered):
        return Comparator.GT, values, False
    if re.match(r"(?:less than|under|below|fewer than)", lowered):
        return Comparator.LT, values, False
    return Comparator.EQ, values, True


def parse_comparison(
    text: str,
) -> tuple[Comparator, Decimal | None, Decimal | None, Decimal | None, str | None]:
    normalized = text.lower().replace("\u2019", "'")
    normalized = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", normalized)
    lowered = " ".join(normalized.split())
    interpretations: set[tuple[Comparator, tuple[Decimal, ...], bool]] = set()
    for template in _COMPARISON_TEMPLATES:
        match = template.fullmatch(lowered)
        if match is None:
            continue
        phrase_match = re.search(_COMPARISON_PHRASE, match.group(0))
        if phrase_match is None:
            return Comparator.NONE, None, None, None, None
        try:
            interpretation = _comparison_from_phrase(phrase_match.group(0))
        except (ValueError, ArithmeticError):
            return Comparator.NONE, None, None, None, None
        if interpretation is None:
            return Comparator.NONE, None, None, None, None
        interpretations.add(interpretation)

    if len(interpretations) != 1:
        return Comparator.NONE, None, None, None, None
    comparator, values, inclusive = next(iter(interpretations))
    if comparator == Comparator.BETWEEN:
        return comparator, None, values[0], values[1], "inclusive" if inclusive else "exclusive"
    return comparator, values[0], None, None, "inclusive" if inclusive else "exclusive"


TZ_MAP = {
    "ET": "America/New_York",
    "UTC": "UTC",
    "EST": "America/New_York",
    "EDT": "America/New_York",
}


def normalize_timezone(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = TZ_MAP.get(value.strip().upper(), value.strip())
    try:
        ZoneInfo(candidate)
    except ZoneInfoNotFoundError:
        return None
    return candidate


class ContractSpecificationParser:
    version = "2"

    def parse(
        self,
        bundle: SemanticsInputBundle,
        now: datetime | None = None,
        llm_confidence: Decimal = Decimal("0"),
    ) -> ContractSpecification:
        now = now or datetime.now(UTC)

        def safe_time(value: Any) -> datetime | None:
            try:
                return parse_time(value, optional=True)
            except UniverseValidationError:
                return None

        market, event, series = bundle.market.fields, bundle.event.fields, bundle.series.fields
        market_rules_hash, market_metadata_hash = material_hashes(market)
        issues = []
        sources, source_issues = normalize_sources(bundle, now)
        issues.extend(source_issues)
        ticker, event_ticker, series_ticker = (
            str(market.get("ticker", "")),
            str(market.get("event_ticker", event.get("event_ticker", ""))),
            str(event.get("series_ticker", series.get("ticker", ""))),
        )
        rules = str(market.get("rules_primary", "")).strip()
        secondary = str(market.get("rules_secondary", "")).strip()
        title = str(market.get("title", "")).strip()
        yes = str(market.get("yes_sub_title") or market.get("yes_proposition") or rules)
        no = str(
            market.get("no_sub_title")
            or market.get("no_proposition")
            or (f"Not: {yes}" if yes else "")
        )
        comparator, threshold, lower, upper, inclusivity = parse_comparison(rules or title)
        # A few frozen historical records contain placeholder rules but a
        # complete authoritative title clause.  Preserve their prior semantic
        # identity without allowing a valid title to override a valid,
        # conflicting rules clause.
        if comparator == Comparator.NONE and rules and title:
            comparator, threshold, lower, upper, inclusivity = parse_comparison(title)
        if comparator == Comparator.NONE:
            issues.append(
                _issue(
                    IssueType.UNKNOWN_LANGUAGE,
                    ("MARKET.title", "MARKET.rules_primary"),
                    "Comparison language is not deterministically supported",
                    now,
                )
            )
        strike = market.get("floor_strike") or market.get("cap_strike")
        if (
            strike is not None
            and threshold is not None
            and exact(str(strike), "strike") != threshold
        ):
            issues.append(
                _issue(
                    IssueType.THRESHOLD_CONFLICT,
                    ("MARKET.rules_primary", "MARKET.strike"),
                    "Rules threshold differs from strike metadata",
                    now,
                )
            )
        timezone = normalize_timezone(market.get("timezone") or event.get("timezone"))
        if timezone is None:
            issues.append(
                _issue(
                    IssueType.TIMEZONE_AMBIGUITY,
                    ("MARKET.timezone", "EVENT.timezone"),
                    "Required timezone is absent or unsupported",
                    now,
                )
            )
        deadline_raw = market.get("deadline") or market.get("expiration_time")
        try:
            deadline = parse_time(deadline_raw, optional=True) if deadline_raw is not None else None
        except UniverseValidationError:
            deadline = None
        if deadline is None:
            issues.append(
                _issue(
                    IssueType.DEADLINE_CONFLICT,
                    ("MARKET.deadline", "MARKET.expiration_time"),
                    "Settlement deadline is unresolved",
                    now,
                )
            )
        settlement = market.get("settlement_value_dollars")
        mve = bool(market.get("mve_collection_ticker") or market.get("multivariate_contract"))
        payout = (
            PayoutModel.MULTIVARIATE
            if mve
            else PayoutModel.SCALAR_OR_PARTIAL
            if settlement not in (None, "0.00", "1.00", "0", "1")
            else PayoutModel.SIMPLE_BINARY
        )
        if payout != PayoutModel.SIMPLE_BINARY:
            issues.append(
                _issue(
                    IssueType.MVE_UNSUPPORTED if mve else IssueType.PAYOUT_MODEL_UNSUPPORTED,
                    ("MARKET.settlement_value_dollars", "MARKET.mve_collection_ticker"),
                    "Payout model is not supported for strategy use",
                    now,
                )
            )
        category = str(series.get("category") or event.get("category") or "").lower()
        station = market.get("station_code")
        if "weather" in category and not station:
            issues.append(
                _issue(
                    IssueType.WEATHER_STATION_MISSING,
                    ("MARKET.station_code",),
                    "Weather station cannot be inferred from city",
                    now,
                )
            )
        early = market.get("early_close_condition")
        if early and not isinstance(early, str):
            issues.append(
                _issue(
                    IssueType.EARLY_CLOSE_AMBIGUITY,
                    ("MARKET.early_close_condition",),
                    "Early-close rule is malformed",
                    now,
                )
            )
        provenance = []

        def prov(field_name: str, layer: SourceLayer, source_field: str, value: Any) -> None:
            provenance.append(
                FieldProvenance(
                    field_name,
                    layer,
                    source_field,
                    None,
                    None,
                    str(value),
                    "deterministic",
                    self.version,
                )
            )

        for field_name, layer, source_field, value in (
            ("yes_proposition", SourceLayer.MARKET, "rules_primary", yes),
            ("no_proposition", SourceLayer.MARKET, "rules_primary", no),
            ("comparator", SourceLayer.MARKET, "rules_primary", comparator.value),
            (
                "settlement_authority",
                sources[0].origin if sources else SourceLayer.SERIES,
                "settlement_sources",
                sources[0].exchange_name if sources else "",
            ),
            ("deadline", SourceLayer.MARKET, "expiration_time", deadline_raw),
            ("timezone", SourceLayer.MARKET, "timezone", timezone),
        ):
            if value not in (None, ""):
                prov(field_name, layer, source_field, value)
        required_provenance = {
            "yes_proposition",
            "no_proposition",
            "comparator",
            "settlement_authority",
            "deadline",
            "timezone",
        }
        missing = required_provenance - {x.field_name for x in provenance}
        if missing:
            issues.append(
                _issue(
                    IssueType.PROVENANCE_MISSING,
                    tuple(sorted(missing)),
                    "Material fields lack provenance",
                    now,
                )
            )
        status = (
            SemanticStatus.VALID
            if not any(x.blocking for x in issues) and yes and no and sources
            else SemanticStatus.UNSUPPORTED
            if payout != PayoutModel.SIMPLE_BINARY
            else SemanticStatus.CONFLICTING
            if any(
                x.issue_type in {IssueType.SETTLEMENT_SOURCE_CONFLICT, IssueType.THRESHOLD_CONFLICT}
                for x in issues
            )
            else SemanticStatus.AMBIGUOUS
        )
        material = {
            "ticker": ticker,
            "event": event_ticker,
            "series": series_ticker,
            "yes": yes,
            "no": no,
            "payout": payout.value,
            "comparison": [comparator.value, str(threshold), str(lower), str(upper), inclusivity],
            "deadline": deadline,
            "timezone": timezone,
            "sources": [x.source_hash for x in sources],
            "rules": rules,
            "secondary": secondary,
            "early": early,
            "strike": [
                market.get("strike_type"),
                market.get("functional_strike"),
                market.get("custom_strike"),
            ],
            "policies": [
                market.get("rounding_rules"),
                market.get("revision_rules"),
                market.get("correction_rules"),
                market.get("recount_rules"),
                market.get("cancellation_rules"),
                market.get("postponement_rules"),
            ],
            "contract_documents": [document.version_hash for document in bundle.documents],
        }
        return ContractSpecification(
            uuid4(),
            ticker,
            event_ticker,
            series_ticker,
            str(market.get("rules_version_id", "unknown")),
            str(market.get("metadata_version_id", "unknown")),
            market_rules_hash,
            market_metadata_hash,
            yes,
            no,
            "exchange_contract",
            payout,
            str(market.get("measured_event_or_value") or title),
            tuple(map(str, market.get("subject_entities", []))),
            market.get("geographic_scope"),
            comparator,
            threshold,
            market.get("threshold_unit"),
            lower,
            upper,
            inclusivity,
            None,
            None,
            deadline,
            timezone,
            safe_time(market.get("occurrence_datetime")),
            safe_time(market.get("expected_expiration_time")),
            safe_time(market.get("expiration_time")),
            sources[0].exchange_name if sources else None,
            sources,
            "CONFLICT"
            if any(x.issue_type == IssueType.SETTLEMENT_SOURCE_CONFLICT for x in issues)
            else "UNRESOLVED"
            if not sources
            else "EXCHANGE_NAMED",
            market.get("rounding_rules"),
            market.get("revision_rules"),
            market.get("correction_rules"),
            market.get("recount_rules"),
            market.get("cancellation_rules"),
            market.get("postponement_rules"),
            early,
            tuple(map(str, market.get("exception_rules", []))),
            market.get("strike_type"),
            market.get("functional_strike"),
            market.get("custom_strike"),
            tuple(x.description for x in issues if x.blocking),
            tuple(
                x.description
                for x in issues
                if x.issue_type
                in {IssueType.SETTLEMENT_SOURCE_CONFLICT, IssueType.THRESHOLD_CONFLICT}
            ),
            tuple(
                x.issue_type.value
                for x in issues
                if x.issue_type in {IssueType.PAYOUT_MODEL_UNSUPPORTED, IssueType.MVE_UNSUPPORTED}
            ),
            llm_confidence,
            status,
            self.version,
            None,
            bundle.source_input_hash,
            _hash(material),
            now,
            None,
            tuple(provenance),
            tuple(issues),
        )


def validate_llm_proposal(proposal: ContractSpecification) -> ContractSpecification:
    """Confidence cannot override any deterministic hard gate."""
    blocking = any(issue.blocking for issue in proposal.issues)
    material = {p.field_name for p in proposal.provenance}
    required = {"yes_proposition", "no_proposition", "settlement_authority", "deadline", "timezone"}
    if (
        blocking
        or proposal.payout_model != PayoutModel.SIMPLE_BINARY
        or not required.issubset(material)
    ):
        return replace(
            proposal,
            semantic_status=SemanticStatus.UNSUPPORTED
            if proposal.payout_model != PayoutModel.SIMPLE_BINARY
            else SemanticStatus.AMBIGUOUS,
        )
    return replace(proposal, semantic_status=SemanticStatus.VALID)
