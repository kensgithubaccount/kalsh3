# M25B7 — Production Live Acceptance Record

## Purpose and result

This record documents the successful real production acceptance performed manually after M25B6 merged.
The bounded manual production read-only smoke used canonical ticker `KXBTCPERP` with the production
environment, explicit `--live-readonly`, and explicit `--confirm-production-readonly`. It performed no
trading or write action.

Final result: `PRODUCTION read-only smoke outcome: SUCCESS`.

The accepted scope is precisely **production read-only Perps observation** through the reviewed M25B6
path. Production execution remains **DISARMED**, production influence remains exactly zero, and trading is
not unlocked.

## Exact safety boundary

The smoke was explicitly human-invoked, limited to one ticker, bounded by the M25B6 connection and
reconnect limits, and allowed zero REST retries. It used only the reviewed read-only REST and margin
WebSocket surfaces. There is no scheduler, autostart, background collection, dashboard control, autonomous
operation, or production-write credential access in this path.

No order, cancel, amend, transfer, or other write endpoint was called. No production execution, risk
authorization, capital allocation, position sizing, live decisioning, strategy influence, or autonomous
trading capability was added or enabled.

## Credential lifecycle result

A dedicated production API key was created with its server-side scope explicitly set to read-only. The
downloaded private key was valid PEM in PKCS#1 form (`-----BEGIN RSA PRIVATE KEY-----`), while the reviewed
`RequestSigner` accepts unencrypted PKCS#8 (`-----BEGIN PRIVATE KEY-----`). The operator used `/usr/bin/openssl` to
convert PKCS#1 to PKCS#8 and streamed the converted key directly into the reviewed enrollment CLI through
file descriptor 3. No converted plaintext key file was written for this step.

Enrollment succeeded as `ENROLLED_UNVERIFIED`. Production verification then succeeded against signed
`GET /trade-api/v2/api_keys`, where the verifier confirmed the enrolled key's server-side scopes were
exactly `["read"]`. The credential state became `VERIFIED_PRODUCTION_READONLY`.

No credential identifier, key material, fingerprint, credential-store content, generated secret, or
encrypted credential-store content is included in this record or committed to the repository.

## Perps entitlement progression and local environment

The public production `/trade-api/v2/margin/markets` surface was available. Signed
`/trade-api/v2/margin/enabled` initially caused the smoke to return `NO_GO` because the account was not yet
Perps/margin enabled. The operator completed Kalshi Perpetuals approval. During a short propagation period,
`NO_GO` remained possible; after entitlement propagated, the smoke passed the entitlement gate and reached
the WebSocket path.

The first entitled WebSocket attempt exposed that the active local Conda environment had not installed the
repository-declared `websockets==16.1.1` dependency. Installing or synchronizing that already-declared
dependency corrected the local environment. This was not a runtime code or dependency-declaration change.

## Persisted acceptance evidence

The untracked local evidence database passed `PRAGMA quick_check = ok`. It was not copied into or committed
to the repository. The accepted counts were:

| Evidence | Count |
|---|---:|
| `perps_market_metadata` rows | 1 |
| `perps_book_evidence` rows | 68 |
| `perps_market_state` rows | 1 |
| Snapshots | 2 |
| Deltas | 66 |
| Distinct connection epochs | 2 |

Production influence was exactly zero in every persisted Perps evidence table:

| Table | `production_influence` |
|---|---:|
| `perps_market_metadata` | 0 |
| `perps_book_evidence` | 0 |
| `perps_market_state` | 0 |

Transient market prices are intentionally not acceptance requirements and are not recorded here.

## What this proves

This live acceptance proves that the reviewed production read-only path can:

1. Load a real `VERIFIED_PRODUCTION_READONLY` credential through the real reviewed
   `ProductionReadCredentialStore`.
2. Authenticate read-only production requests.
3. Fetch real production Perps market metadata for `KXBTCPERP`.
4. Confirm real Perps/margin entitlement through the signed GET-only entitlement endpoint.
5. Connect to the production margin WebSocket using the reviewed GET/HEAD-only signer.
6. Receive genuine production order-book snapshots, deltas, and ticker state.
7. Survive the bounded reconnect requirement with two distinct connection epochs and fresh snapshots.
8. Persist genuine production evidence in the append-only Perps evidence store.
9. Preserve `production_influence` exactly zero.

## What this does not prove or unlock

- No orders were placed.
- No order, cancel, amend, transfer, or other write endpoint was called.
- Production execution remains `DISARMED`.
- Production-write credentials remain separate and unavailable to this path.
- No autonomous or background live collector is enabled.
- No scheduler or autostart exists for this smoke.
- No strategy receives production influence from this evidence yet.
- No capital allocation, position sizing, live decisioning, or autonomous trading is unlocked.
- This does not establish profitability or trading edge.
- This is production observation acceptance only.

No secrets, credentials, local paths, generated state, or SQLite evidence database are committed by M25B7.
