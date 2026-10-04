# Perps Live Market-Version Missing-Field Drift — 2026-10-04

Status: **READ-ONLY AUTHORITY REPAIR / NO EXECUTION AUTHORITY**

## Point-in-time production evidence

Temporary no-secret probe PR #177 queried the same production Margin REST origin
used by the repository's read-only client. It established that the active Bitcoin
Margin market is `KXBTCPERP`; the guessed identifiers `BTC-PERP` and `BTCPERP`
both returned HTTP 404.

The first probe printed `market.get("market_version")`, which returns `None`
for both an absent key and an explicit JSON null. That output therefore did not
establish which schema shape production actually used.

Follow-up temporary probe PR #179 tested key membership explicitly and ran the
canonical parser with the same exact-Decimal JSON decoding used by the production
read-only client.

At 2026-10-04T19:43Z:

- `GET /trade-api/v2/margin/markets/KXBTCPERP` returned HTTP 200.
- Exact response length: 1025 bytes.
- Point-in-time response SHA-256:
  `608583154630ffd65e87bb81bc849a468632dca197db4d9caddf8d869579bad0`.
- The returned market key set did **not** contain `market_version`.
- Returned market identity included:
  - `ticker = KXBTCPERP`
  - `status = active`
  - `exchange_index = 0`
  - `underlying_multiplier = 1.000000`
  - `contract_size = 0.000100`
  - `tick_size = 0.0001`
  - `asset_class = Crypto`.
- The repaired canonical parser accepted the complete live payload using production
  decoding semantics.
- Resulting structural contract hash:
  `45efa1b60983eada39a5f51d6275d0ed6745d5bcd1eaf60dd8364b7846d537e1`.
- Resulting full metadata hash:
  `05606a6e635d7f22027e160784237c144b9dbcdddb63c15412114c86023e7d59`.
- The parsed market remained active/open.

The response body contains volatile market fields, so its full-body SHA-256 is
point-in-time evidence rather than a permanent expected hash.

## Authority conflict

The first-party Perps OpenAPI bytes captured on 2026-10-04 have SHA-256
`d13cb9c5c18cbb9ab2fe60d173c74511dea627a89321d17f7b0505a88f82aeb0`
and describe `market_version` as a required integer field.

Live production currently omits that field from this active market. This is a
point-in-time live-schema divergence from the published type contract.

## Read-only repair decision

For read-only research evidence only:

- a present `market_version` must remain an exact positive int32;
- the key being absent is preserved as **UNKNOWN**;
- explicit JSON null remains invalid;
- booleans, strings, zero, negative values, and out-of-range integers remain invalid;
- no synthetic/default version is ever created;
- the raw normalized snapshot preserves the field's absence;
- the structural contract hash uses a missing-field sentinel, so an absent version
  cannot collapse onto an explicit null or a concrete integer;
- book and ticker-state evidence carry UNKNOWN as `None` in immutable evidence
  identity;
- fresh append-only evidence stores preserve UNKNOWN as SQL NULL.

A later appearance or change of a concrete version changes contract/full-metadata
identity and therefore invalidates assumptions that require the prior identity.

Existing evidence databases are not silently migrated. A legacy table whose old
schema rejects SQL NULL continues to fail closed. The prospective collector uses
a fresh database per session.

## Execution boundary

This repair grants no order, alert, capital, or production-write authority.
Any future order-capable milestone must separately establish how a concrete
exchange `market_version` is obtained and bound when required. UNKNOWN must never
be replaced with an invented version or used as execution authorization.

Production influence remains zero.
