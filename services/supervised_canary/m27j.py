"""M27J -- authoritative CURRENT live market-rules identity, for M27I's ``rules_current`` gate.

CODE / TEST / REVIEW ONLY. This module never arms production, never sends/cancels/amends/
decreases an order, never touches the production write credential, and never creates an
authenticated write path. It performs exactly one thing: a single bounded, unauthenticated,
PUBLIC GET of the exact-market endpoint for one candidate ticker (via
:mod:`services.market_universe.market_snapshot`), and independently re-derives that market's
canonical rules identity so it can be compared -- fresh, right now -- against the *expected*
rules identity M27I's authoritative economics binding independently re-derived (see
:mod:`services.opportunity_engine.authoritative_economics`). This module never compares against
a bare caller-supplied string on ``MarketEconomicsEvidence`` -- see the M27I integration notes in
:mod:`services.supervised_canary.m27i` for why that alone cannot prove continuity.

Dependency boundary: this module imports only :mod:`services.market_universe.market_snapshot` --
never :mod:`scripts.m27e_public_read_acceptance` or anything else under ``scripts/``. Production
services never depend on the operator CLI layer; the dependency direction is always
``scripts -> services``.

Hash-lineage acceptance criterion: this module never invents a second rules-hash algorithm. The
only authority for "current rules identity" is
:meth:`services.market_universe.domain.Market.rules_hash`, produced by the exact same frozen
:func:`services.market_universe.domain.material_hashes` /
:data:`services.market_universe.domain.RULE_FIELDS` this repository already uses everywhere else
a rules hash is computed.

Read-only safety: this module issues only ``GET`` requests, reuses the fixed-origin, bounded,
no-redirect transport already reviewed for M27E (via
:mod:`services.market_universe.public_read`), sends no credentials, and never touches
:mod:`services.production_execution.security_boundary` or
:mod:`services.production_execution.transport`.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from services.market_universe.market_snapshot import (
    FRESHNESS,
    HOST,
    PARSER_VERSION,
    SCHEMA,
    AuthoritativeMarketSnapshot,
    acquire_market_snapshot,
    validate_market_snapshot,
)

__all__ = [
    "FRESHNESS",
    "HOST",
    "PARSER_VERSION",
    "SCHEMA",
    "RulesCurrentResult",
    "acquire_current_market_rules",
    "main",
    "validate_current_rules_for_candidate",
]


def acquire_current_market_rules(
    ticker: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: Callable[[str], tuple[dict[str, object], bytes]] | None = None,
) -> AuthoritativeMarketSnapshot:
    """Thin current-side wrapper over :func:`acquire_market_snapshot`. See module docstring."""
    if transport is None:
        return acquire_market_snapshot(ticker, clock=clock)
    return acquire_market_snapshot(ticker, clock=clock, transport=transport)


@dataclass(frozen=True, slots=True)
class RulesCurrentResult:
    classification: str
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def validate_current_rules_for_candidate(
    payload: object,
    *,
    expected_market_ticker: str,
    expected_event_ticker: str,
    expected_rules_hash: str,
    now: datetime,
) -> RulesCurrentResult:
    """Independently re-validate a serialized current-side :class:`AuthoritativeMarketSnapshot`.

    Delegates every shape/origin/envelope/body-hash/re-parse check to
    :func:`services.market_universe.market_snapshot.validate_market_snapshot` -- this module
    never re-derives rules identity itself. Layers exactly two things on top: ``now``-relative
    30-second freshness, and the final comparison against ``expected_rules_hash``. Callers in
    M27I must supply an ``expected_rules_hash`` that itself came from independently re-validating
    an authoritative economics binding (see
    :mod:`services.opportunity_engine.authoritative_economics`) -- never a bare
    ``economics.market_rules_hash`` string, which a caller can set to anything.

    PASS only if the underlying snapshot validates, is fresh right now, and its independently
    re-derived ``rules_hash`` -- never the merely stamped one -- exactly equals
    ``expected_rules_hash``.
    """
    result = validate_market_snapshot(
        payload,
        expected_ticker=expected_market_ticker,
        expected_event_ticker=expected_event_ticker,
    )
    if not result.succeeded:
        return RulesCurrentResult(result.classification, result.reason)
    observed_at = result.observed_at
    if observed_at is None:  # pragma: no cover - PASS always carries observed_at
        return RulesCurrentResult(
            "MALFORMED_CURRENT_RULES_EVIDENCE", "snapshot validation result incomplete"
        )
    expires_at = observed_at + FRESHNESS
    if observed_at > now:
        return RulesCurrentResult(
            "MALFORMED_CURRENT_RULES_EVIDENCE", "evidence observed_at is in the future"
        )
    if not (observed_at <= now <= expires_at):
        return RulesCurrentResult(
            "RULES_EVIDENCE_STALE", "evidence is not fresh at consumption time"
        )
    if result.rules_hash != expected_rules_hash:
        return RulesCurrentResult(
            "RULES_IDENTITY_CHANGED",
            "current live rules identity no longer matches the expected authoritative rules hash",
        )
    return RulesCurrentResult("PASS", None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "M27J read-only current live market-rules identity acquisition. PUBLIC GET only: "
            "no credentials, fixed production origin, one candidate ticker, HTTP 200 only."
        )
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    evidence = acquire_current_market_rules(args.ticker)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence.to_json(), sort_keys=True, indent=2) + "\n")
    print(f"classification={evidence.classification} rules_hash={evidence.rules_hash}")
    print("PRODUCTION_WRITE_CREDENTIAL: NOT INSTALLED  PRODUCTION_ARMED: DISARMED")
    return 0 if evidence.succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
