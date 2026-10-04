# Perps Live Market-Version Drift — 2026-10-04

Status: **READ-ONLY AUTHORITY REPAIR / NO EXECUTION AUTHORITY**

## Point-in-time production evidence

Temporary probe PR #177 performed unauthenticated, read-only GETs against the
same production Margin REST origin used by the repository's read-only client.
The probe contained no credentials and is closed without merge after evidence
capture.

At 2026-10-04T19:21Z:

- `GET /trade-api/v2/margin/markets` returned HTTP 200.
- The active Bitcoin row had ticker `KXBTCPERP`.
- `GET /trade-api/v2/margin/markets/KXBTCPERP` returned HTTP 200.
- Exact detail response length: 1025 bytes.
- Exact detail response SHA-256:
  `78451a54ef86862faa5bafdae596a03a9f6697a7c3a524ba91e7e140f2c10662`.
- Returned fields included:
  - `ticker = KXBTCPERP`
  - `status = active`
  - `title = 0.0001 BTC`
  - `exchange_index = 0`
  - `market_version = null`
  - `underlying_multiplier = 1.000000`
  - `contract_size = 0.000100`
  - `tick_size = 0.0001`
  - `asset_class = Crypto`.

Both guessed identifiers `BTC-PERP` and `BTCPERP` returned HTTP 404.

## Authority conflict

The first-party Perps OpenAPI bytes captured on 2026-10-04 have SHA-256
`d13cb9c5c18cbb9ab2fe60d173c74511dea627a89321d17f7b0505a88f82aeb0`
and list `market_version` as a required integer field.

The live production payload includes the required key but currently supplies
JSON null. This is a point-in-time live-schema divergence from the published
type contract.

## Read-only repair decision

For read-only research evidence only:

- `market_version` remains a required key;
- an exact positive int32 remains valid;
- explicit JSON null is also preserved as **UNKNOWN**;
- missing, boolean, string, zero, negative, or out-of-range values fail closed;
- no synthetic version is ever created;
- null participates literally in the structural contract hash and full metadata
  hash;
- book/ticker evidence preserves null in its immutable evidence identity;
- fresh append-only evidence stores preserve SQL NULL.

A transition from null to an integer, or between integer versions, therefore
changes structural identity and invalidates assumptions that require the prior
identity.

Existing evidence databases are not silently migrated. A legacy table whose
older schema rejects SQL NULL continues to fail closed. The prospective
collector uses a fresh database per session.

## Execution boundary

This repair grants no order, alert, capital, or production-write authority.
Any future order-capable milestone must separately determine how exchange
`market_version` is to be bound for execution; UNKNOWN must never be replaced
with an invented version.

Production influence remains zero.
