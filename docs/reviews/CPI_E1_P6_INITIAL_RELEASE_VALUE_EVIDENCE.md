# CPI-E1-P6 — Reviewed Initial-Release CPI Value Evidence

## Scope

P6 proves one narrow BLS observation: CPI-U, U.S. city average, all items,
seasonally adjusted, change from the preceding month, expressed as the exact
one-decimal percentage printed in the initial archived release. It accepts only
validated acquisition-bound P4 or P5A issuance and preserves the exact upstream
acquisition evidence ID and mode; `MANUAL_BROWSER_ATTESTED` remains distinct
from automated HTTPS.

## Dual representation and period binding

The parser requires exactly one release headline, exactly one CPI-U narrative
headline, and exactly one canonical Table A seasonal-adjustment structure. The
narrative and Table A `All items` row must produce equal `Decimal` values with
one decimal place. The release headline month, narrative month, and final
current Table A month header must agree. Archive URL dates are not used as the
CPI reference period.

Missing tables, missing narrative, wrong population/basket, core or NSA values,
earlier columns, duplicate conflicting structures, malformed precision, and
disagreement fail closed.

## Trust boundary

`issue_cpi_initial_release_observation` takes a complete P4/P5A bound issuance,
not a value or raw bytes. The issuer revalidates acquisition provenance, exact
artifact bytes/hash, P1 authority, release artifact, and P2 publication binding
before parsing. The immutable observation carries acquisition, artifact,
publication, timing, parser-policy, and content-addressed identities. It is
permanently `research_only = true` with `production_influence = 0`; direct
construction, replacement, mutation, and public rehashing do not create
authority.

## Empirical targets

The reviewed P5A receipt is the required empirical boundary:

| Release | P5A SHA-256 | Expected value |
|---|---|---|
| July 2025 | `5b869d4365bc0f58db9814e3da09105f0fd944e4bbf16c39b5511f774a03dc4b` | `Decimal("0.2")` |
| December 2025 | `8351af0db99f8b1e338abe1b33cb062a70e61d2b154c0ec26aaed964f52b489e` | `Decimal("0.3")` |
| January 2026 | `3b46aebecd5aa2d66f6f8abc38e47381e180a73db6cf87313ecc8eeddebd69f8` | `Decimal("0.2")` |

P6 is BLS observation truth only. It does not reconcile Kalshi, create a
settlement record, promote G3, train or score a model, calculate economics, or
authorize execution. The inherited P5A name `actual_bot_ingest_at` is retained
as non-blocking semantic debt; P6 uses acquisition-mode-neutral terminology.
