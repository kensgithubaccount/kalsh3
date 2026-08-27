# CPI-E1-P1 BLS Source Governance

Status: **RESEARCH-ONLY SOURCE AUTHORITY PREREQUISITE**. CPI-E1-P1 does not acquire CPI observations, does not promote any research gate, and does not establish settlement or point-in-time truth.

## Canonical source-governance audit

The canonical authority pattern was audited before implementation. `services/forecasting/weather_source_authority.py` is the strongest source-authority precedent: fixed reviewed policy rows, non-caller-constructible frozen authority objects, content-addressed policy identity, fail-closed resolution, and zero production influence. Its weather identifiers and settlement semantics are domain-specific and are not reused as CPI authority. `services/production_weather_strategy/forecast_vintage.py` separates source artifacts from publication/vintage proof; it is not a BLS issuer. `services/historical_replay/domain.py` provides generic availability structures but construction of those structures is not CPI source or PIT authority. `services/document_intelligence/evidence.py` and `models.py` provide generic evidence representation, not a reviewed BLS allowlist. `docs/source_policy.md` requires official or explicitly permitted interfaces and exact allowlisting but is policy guidance, not a caller-populated source registry.

`services/market_universe/empirical_researchability.py` and `researchability_hard_gates.py` remain unchanged. `services/contract_intelligence/specification.py` already carries contract semantics and does not justify introducing broad CPI-wide contract enums. No suitable generic reviewed source-authority registry exists, so P1 adds one CPI-specific module rather than a second generic registry.

## Exact reviewed CPI source profile

The sole positive profile is:

- CPI-U (Consumer Price Index for All Urban Consumers)
- U.S. city average
- all items / headline CPI
- seasonally adjusted
- signed one-month percentage change
- initial release

This is a source-evidence profile only. It is not proof that a Kalshi market binds to these semantics. BLS's CPI release tables explicitly present CPI-U, U.S. city average, all items, and seasonally adjusted one-month changes; the current release also distinguishes all-items from all-items-less-food-and-energy and from 12-month changes.

## Positive BLS source roles and exact locator rules

Source organization is fixed to **U.S. Bureau of Labor Statistics** and source product to the **Consumer Price Index news release**. Authority is limited to two roles on the exact HTTPS origin `https://www.bls.gov`; hostname or source name alone is never enough.

### Historical initial-release document

Reviewed interface: archived CPI news release HTML only.

Exact locator shape:

`https://www.bls.gov/news.release/archives/cpi_MMDDYYYY.htm`

The resolver requires `https`, exact netloc `www.bls.gov`, no user info, no port, no query, no fragment, the exact archive path pattern, and a valid calendar date. The BLS archived-release index links historical CPI releases using this HTML shape, and individual archived releases contain the CPI release text. PDF, TXT, the rolling current-edition URL, API paths, other BLS releases, alternate hosts/subdomains, and arbitrary BLS documents are not authorized by P1.

Positive authority: the archived release is an eligible primary source for the CPI-U U.S. city average all-items seasonally adjusted signed one-month percentage change printed in that release. Release material may later be inspected by P2/resumed CPI-E1 for an independently evidenced exact embargo/release instant.

It does **not** by its existence prove `published_at`, `replay_available_at`, actual first-public server time, original acquisition time, revision number/lineage, G4 PASS, or Kalshi settlement truth. P1 does not parse or mint a timestamp.

### CPI release calendar / schedule

Reviewed interface: CPI-specific BLS schedule HTML.

Exact locator:

`https://www.bls.gov/schedule/news_release/cpi.htm`

The BLS page is specifically titled “Schedule of Releases for the Consumer Price Index” and lists reference month, release date, and release time. P1 rejects the broader BLS calendar/ICS product, other release schedules, query-bearing variants, non-HTTPS locators, and alternate hosts.

Positive authority: eligible evidence for the **scheduled** CPI release date/time.

It does **not** prove the released CPI value, actual first-public server time, or the actual release time if publication was delayed/rescheduled. It does not mint `published_at` or `replay_available_at` and does not prove Kalshi settlement truth.

## Current BLS Public Data API posture

The BLS Public Data API is outside positive P1 authority. BLS documents it as an interface for retrieving published historical time-series data. That is useful for later cross-checking but does not independently identify the exact archived initial release, prove its first-public instant, prove original-vintage identity/revision lineage, or establish PIT availability. P1 therefore creates no API role and the resolver rejects `api.bls.gov/publicAPI/...` for the historical-initial-release role.

## Why names and families are insufficient

`BLS`, `bls.gov`, `ReleaseTarget.CPI`, `KXCPI`, title/category, and `BINARY_THRESHOLD` do not encode the exact source product, source role, or reviewed interface and cannot resolve authority. The source profile is deliberately not keyed by a Kalshi ticker.

`KXCPIYOY`, `KXCPICORE`, and `KXCPICOREYOY` are semantically different domains (year-over-year and/or core CPI) and do not inherit this profile. NSA CPI, index-level contracts, another CPI population, and another BLS statistic are likewise outside the profile.

## Issuance and validation boundary

`ReviewedCPISourceAuthority` is frozen, slotted, and not ordinarily caller-constructible. Two immutable canonical policy rows are issued internally into a read-only mapping. Profile, source role, and source interface require exact enum identity; equal plain strings do not substitute. Policy identity is content-addressed from the complete fixed rows, and each role authority has a content-addressed identity derived from its exact canonical row.

Consumers must call `validate_cpi_source_authority` or the resolver. Validation checks exact runtime type, exact enum identity, canonical object issuance, the independently reconstructed fixed row, content identities, `research_only=True`, and `production_influence=0`. Hash content is not authority: `dataclasses.replace`, `object.__setattr__` semantic mutation, reconstruction, and mutate-plus-rehash do not survive validation.

## P2 compatibility boundary

P1 only preserves the distinction needed by a later conservative PIT policy. A release document may be eligible material from which a later step independently evidences an exact BLS release/publication/embargo instant. P1 neither decides that an instant exists nor constructs `Availability`. If only a release date is evidenced, P1 makes no availability claim. P2 remains separate.

## Gate and empirical posture

G1 UNKNOWN  
G2 UNKNOWN  
G3 UNKNOWN  
G4 UNKNOWN  
G5 UNKNOWN  
G6 UNKNOWN

P1 does not modify A3.2/A4 gate logic, acquire a historical CPI corpus, create a dataset manifest, create historical settlement labels, construct empirical vintages, run a model, compute EV/economics, or introduce lifecycle/execution authority. BLS observation remains distinct from Kalshi determination/final settlement.

All P1 authority is `research_only=True` with `production_influence=0`.

## Smallest next checkpoint

Complete focused independent CPI-E1-P1 review of the exact policy rows, resolver attack surface, changed-file scope, verification, and exact-head CI. Do not merge from the writer, do not acquire CPI data, and do not begin P2 during P1 review.
