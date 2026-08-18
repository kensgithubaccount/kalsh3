# M27E — Live Supervised Canary Readiness

Date: 2026-08-17 (follow-up acceptance)  
Branch: `feat/m27e-live-readiness`  
Production mutations: none

## Dependency graph

`M27D candidate` → `M13 deterministic authorization` → `M16 preview/step-up approval` →
`M15 immutable execution envelope/journal` → `isolated signer` →
`fixed Kalshi HTTPS transport` → `reconciliation`.

M27D remains shadow-only: one CLIMDW contract, one global v1 submission, August 18–31,
exact 20 percentage-point discrepancy, boundary-mass rejection, unvalidated GHCN proxy
warning, explicit human acknowledgement, and no autonomous trading.

## Implemented in M27E

- M15 RSA-PSS now preserves Linux anonymous `memfd` behavior and has a macOS-safe pipe-
  backed inherited-descriptor fallback. Neither path creates a persistent key file or
  places key material in argv, environment, logs, or the repository.
- `FixedKalshiProductionTransport` restricts requests to
  `https://external-api.kalshi.com`, TLS verification, typed order lifecycle paths,
  exact body bytes, three-second timeout, one-megabyte response bound, and no redirects.
  It does not consult proxy environment variables and has no arbitrary URL interface.
- Protected operator enrollment validates production/subaccount 0, exact `read` +
  `write::trade` scope (not broad `write`), owner/password/TOTP/CSRF gates, and the exact
  real-money warning before atomic sealed storage. Installation additionally requires an
  authenticated `GET /trade-api/v2/api_keys` self-lookup proving the candidate key's own
  server-reported scopes, subaccount, and identity -- a caller-declared claim alone is never
  sufficient. Tests may use `fixture_only` synthetic credentials; M27E did not invoke it.
- `python -m services.supervised_canary.readiness_report` prints each M16 gate separately.
  Missing evidence is `NOT TESTED`; the absent write credential is `NOT INSTALLED`.

## Non-secret acceptance completed

- PostgreSQL 18.6 and psycopg were installed into the non-root `kalsh3` conda environment.
  A temporary cluster was bound to localhost port 55432 outside the repository. The
  PostgreSQL adapters now exercise the M15 journal and M16 canary counter/session
  invariants against actual PostgreSQL transactions.
- PostgreSQL acceptance passed: schema initialization, reconnect uniqueness, concurrent
  thread and process races, transaction rollback, restart/reconnect, ambiguous submission
  recovery, unresolved-canary uniqueness, and global one-submission burn.
- An actual PostgreSQL server restart preserved a synthetic M15 journal row
  (`restart-exec`) and the M16 `SUBMITTED_OR_UNKNOWN` recovery state; the cluster was then
  stopped and removed.
- Current official documentation pages for exchange status, markets, events, orderbook,
  balance, orders, positions, fills, and all four V2 order operations were fetched and
  hashed. All 13 pages returned HTTP 200. Compatibility artifact SHA256:
  `a34dd4b2e8e3d8dd57b29caac6f630f3c783fcc357aba0110868603bb1991e53`.
- Fresh unauthenticated production reads succeeded: exchange status HTTP 200 and a
  complete one-page `series_ticker=CLIMDW&status=open&limit=1000` market query HTTP 200
  with zero markets. The batch began at `2026-08-18T00:50:25.202519+00:00` UTC. Public evidence artifact SHA256:
  `c68a70c898f9b122bbb82921ddf04b819c3e7088115705724550a68280c86b87`.
- The separate public `GET /series/CLIMDW` returned HTTP 404. This is recorded as an
  HTTP result, not interpreted as an empty series or zero markets.
- Fresh M27D shadow result: `ABSTAIN_NO_OPEN_MARKET`, because the valid complete open
  CLIMDW query returned zero markets. No forecast/economics or order path was invoked.

## Current API contract

The fixed production authority remains
`https://external-api.kalshi.com/trade-api/v2`. Current official pages confirm the
exchange status, market/event/orderbook, balance, orders, positions, fills, and V2
create/cancel/amend/decrease paths. The current V2 create endpoint is
`POST /portfolio/events/orders`, with string fixed-point `price` and `count`, `bid`/`ask`
YES-leg orientation, and subaccount/exchange index fields. Authentication signs
`timestamp + HTTP_METHOD + full path` with the query string excluded. The internal
`cancel_newest` policy label is translated to current wire value `taker_at_cross`.
No authenticated or mutating compatibility call was made.

Authenticated production reads remain blocked by the intentionally absent approved read
credential. An authentication failure is never interpreted as an empty account.

The transport implementation is reviewed by synthetic/local tests and current official
V2 documentation. No production transport call, POST, DELETE, PATCH, order, or mutation
was attempted.

## Enrollment boundary

The future operator action is an authenticated HTTPS/operator workflow that supplies the
credential through the protected input channel, verifies exact scopes (`read` +
`write::trade`, never broad `write`) and subaccount 0, confirms:

`INSTALL PRODUCTION WRITE CREDENTIAL — REAL MONEY — ONE CONTRACT ONLY`

and then atomically installs sealed material in the isolated signer store. Installation is
gated on an authenticated `GET /trade-api/v2/api_keys` call signed by the candidate key
itself: the response must contain exactly one entry for the supplied key ID whose
server-reported `scopes` equal `{"read", "write::trade"}` exactly and whose `subaccount`
equals `0` (a null/unrestricted subaccount is rejected, not treated as broader authority).
Any disagreement between the caller-declared claim and this server-reported metadata, any
HTTP failure, 401/403, redirect, or malformed response leaves the credential uninstalled
and production DISARMED. It must be run only after the remaining M16 gates pass. Do not
paste a PEM into chat or shell history. M27E did not run this action; no write credential
is installed.

### Why `read` + `write::trade` (not broad `write`)

Per current official Kalshi API-key documentation (`docs.kalshi.com/api-reference/api-keys`),
`write` is a broad parent scope granting every write endpoint, including fund transfers
(`write::transfer`) and block-trade acceptance (`write::block_trade_accept`). `write::trade`
is an independently grantable child scope limited to order create/cancel/amend/decrease --
exactly the M16 supervised-canary trade-lifecycle surface (`POST`/`DELETE`
`/portfolio/events/orders[/{id}[/amend|/decrease]]`) -- with no transfer or block-trade
authority. `read` (the broad read parent) covers every read endpoint the canary uses:
balance, orders, positions, fills, and settlements/limits. No M16 code path calls a
transfer or block-trade endpoint, so those scopes are excluded by construction, not by
omission.

## Frozen weather boundary

No frozen weather files were changed, no retraining occurred, no prospective GHCN outcomes
were acquired, and no TWC settlement-equivalence claim was made.

## Two verdicts

`IMPLEMENTATION_REVIEW_STATUS = SAFE FOR INDEPENDENT REVIEW`  
`REAL_MONEY_CANARY_READINESS = BLOCKED`

The implementation verdict covers the non-secret code/runtime evidence. Real-money
readiness remains blocked by credential-required and human/deployment gates.

## Verification

- Focused M15/M16/M27D/M27E tests: **passed**.
- PostgreSQL integration: **3 passed**, including concurrent processes.
- Official API compatibility pages: **13/13 HTTP 200**.
- Fresh public exchange/market evidence: **PASS**, complete pagination.
- Authenticated production read: **BLOCKED_BY_CREDENTIAL**.
- Real production mutation: **NOT ATTEMPTED**.
- Production credential installed: **NO**.
- Production armed: **NO**.

## Review disposition

**SAFE FOR INDEPENDENT REVIEW**. This does not authorize credential enrollment, arming,
or any production mutation.

## Future credential handoff (not run)

This procedure is nine distinct steps. It is written from current official Kalshi API-key
documentation and this repository's actual enrollment code; it does not invent UI
functionality that isn't documented.

1. **Bootstrap key creation (Kalshi UI).** Official Kalshi Quick Start documentation
   (`docs.kalshi.com/getting_started/api_keys`) describes creating an API key through the
   Kalshi website: Account Settings → Profile Settings (`kalshi.com/account/profile`) →
   the "API Keys" section → "Create New API Key". That flow returns a `Key ID` and, once,
   a downloadable `Private Key`. **Current official documentation does not establish that
   this consumer UI flow exposes explicit scope or subaccount-restriction controls at
   creation time** -- the Quick Start page describes only receiving and downloading key
   material, not choosing scopes/subaccount. Do not assume the UI can produce an
   already-restricted key unless that is independently verified against the UI actually
   presented to the operator at the time.
2. **Least-privilege canary key (Generate/Create API Key REST endpoint).** Official REST
   documentation (`docs.kalshi.com/api-reference/api-keys/generate-api-key` and
   `.../create-api-key`) documents `POST /trade-api/v2/api_keys/generate` (and the
   caller-supplied-public-key variant `POST /trade-api/v2/api_keys`) accepting an explicit
   `scopes` array and an explicit `subaccount` (0-63) at creation time -- this is where
   `scopes = ["read", "write::trade"]` and `subaccount = 0` are actually documented as
   creatable. **Both endpoints themselves require the three `KALSHI-ACCESS-*` headers,
   i.e. an already-authenticated API key**, to call. Two honest paths follow from that:
   - If the Kalshi UI presented to the operator *does* expose the required scope/subaccount
     controls directly, the operator may create the final restricted canary key there
     without a separate REST call. Our documentation does not assume this is true; the
     operator must confirm it against the UI actually in front of them.
   - Otherwise, the step-1 bootstrap key (whatever scope Kalshi's UI grants by default) is
     used, once, purely as authenticated access to call the Generate/Create API Key REST
     endpoint with `scopes=["read","write::trade"], subaccount=0`, producing the actual
     restricted canary key. **Do not silently retain the broad bootstrap credential.**
     Handle it as a separate operator security step: revoke or remove it in Kalshi's
     key-management UI once the narrowly restricted canary credential exists and has been
     independently verified by step 3 below, unless another separately reviewed
     operational need requires keeping it.

   **M27E does not implement automatic API-key generation.** Calling either REST endpoint
   to mint or mutate a key is outside this milestone's scope; this repository's enrollment
   code only ever accepts an already-created candidate credential and verifies it -- it
   never creates or mutates a Kalshi API key itself.
3. **Server-metadata validation (this repository, at enrollment time).** Our local
   enrollment command does not trust the operator's claim about what was selected in step
   1 or 2. It authenticates as the candidate key and calls `GET /trade-api/v2/api_keys`
   (`services.production_execution.enrollment.verify_live_write_credential_authority`),
   finds the single entry matching the supplied key ID, and requires its server-reported
   `scopes` to equal `{"read", "write::trade"}` exactly and its `subaccount` to equal `0`
   (`require_live_write_authority`). A missing key, a duplicate/ambiguous key ID, an
   unrestricted (`null`) or non-zero subaccount, any additional/unknown scope, a redirect,
   a 401/403, an HTTP failure, or a malformed response all fail installation before
   anything is written to disk. Documented scope semantics (`read` grants every read
   endpoint, including `GET /api_keys`) make it reasonable to expect a correctly scoped
   candidate key can self-query this endpoint -- but that this specific authenticated call
   actually succeeds for a real key is unverified. It is a **live credential acceptance
   item**, not something this milestone has tested, and it is never assumed to have
   passed before a real candidate credential exists. If a real attempt later returns
   401/403, enrollment stays `NOT INSTALLED`, production stays `DISARMED`, and the scope
   requirement is never automatically weakened or retried with broader scopes to work
   around the failure -- a 401/403 there is treated as a verification failure, not as
   grounds to grant more authority.
4. **Key ID input.** The key ID is not secret; it may be supplied as a command-line
   argument or read from a small operator-supplied config file. It is never the PEM.
5. **Private-key input (no argv/env/chat/shell-expanded secret).** The PEM is supplied only
   through an inherited file descriptor that the shell opens directly from the downloaded
   file -- it is never interpolated into an argument, an environment variable, a chat
   message, or shell history. A syntactically valid macOS (zsh or bash) example:

   ```sh
   python -m services.production_execution.enrollment_cli \
     --key-id-file /secure/operator/key_id.txt \
     --private-key-fd 3 \
     --store-dir /secure/kalsh3/write \
     3< /secure/operator/key.pem
   ```

   The `3< /secure/operator/key.pem` redirection opens the file on descriptor 3 for the
   duration of that one command only; the enrollment CLI reads the PEM bytes from fd 3
   (`services.kalshi_account_gateway.production_read_credentials.read_private_key_fd`
   models the same pattern for the existing read credential) and the shell never expands
   the file's contents into the command line or environment.
6. **Sealed credential location.** A successful install writes only two files under an
   owner-only (`0700`) directory supplied by `--store-dir` (for example
   `/secure/kalsh3/write`, outside the repository): `master.key` (a random 32-byte key,
   mode `0600`) and `credential.enc` (the key ID and PEM sealed with that master key via
   `SecretBox`, mode `0600`). Nothing is written anywhere else, and the returned
   `InstallationReceipt` carries only hashes/fingerprint/timestamp/environment/account/
   scopes -- never the PEM itself (`repr()` is overridden to `<secret-free>`).
7. **Authenticated READ acceptance before activation.** Before the write credential is
   used for anything beyond the install-time metadata check in step 3, run authenticated
   read acceptance: balance, limits, and complete orders/positions/fills/settlements
   pagination, exchange status, freshness, and reconciliation (the M25B5
   `ProductionReadCredentialStore` verify path already models this for the separate
   read-only credential; the write credential's own reads must pass the same bar). Any
   failure leaves production DISARMED; a read failure is never interpreted as an empty
   account.
8. **Enrollment rollback.** `ProtectedWriteCredentialStore.install` writes `master.key`
   first, then atomically writes `credential.enc`; if sealing/writing the second file
   raises for any reason, both files are unlinked before the exception propagates, so a
   failed install never leaves a half-written credential on disk. Every validation gate
   (owner/password/TOTP/CSRF, exact confirmation phrase, exact scopes, subaccount 0, and
   the step-3 server-metadata proof) runs before `install()` is ever called, so a rejected
   enrollment never touches the store directory at all.
9. **Local removal and Kalshi-side revocation.** To remove locally, delete the store
   directory's two files (or the directory) so `PRODUCTION_WRITE_CREDENTIAL` reports `NOT
   INSTALLED` again, and separately revoke the API key in Kalshi's authenticated
   key-management UI. Local deletion and Kalshi-side revocation are independent actions;
   this milestone makes no revocation API call, so local removal alone does not invalidate
   the key at Kalshi.
