# ADR 0003: production signer uses a sign-and-send security boundary

## Status

Accepted for the M15 offline implementation. Production remains DISARMED and no write credential is installed.

## Problem

Kalshi authentication signs `timestamp_ms + uppercase_method + request_path_without_query` with RSA-PSS/SHA-256. The request body is not covered by the exchange signature. A sign-only service could validate approved body A and return headers that a compromised gateway attaches to body B. Internal body hashes improve detection but do not make that separation impossible.

## Options reviewed

### A. Sign-only

This minimizes signer egress and network blast radius, but leaves body-substitution resistance dependent on the downstream gateway. It has simple operations and testing, yet raw headers are replayable until timestamp rejection and the key-bearing boundary cannot prove which bytes were transmitted.

### B. Sign-and-send

The isolated boundary validates the typed envelope and M13 authorization, owns canonical serialization, generates the timestamp, signs the allowlisted method/path, and passes those same immutable body bytes to a fixed-origin TLS transport. It returns only a normalized outcome, never a signature. This expands controlled egress and operational responsibility, but gives the strongest body binding, smallest key exposure, no signing oracle, clearer audit lineage, and deterministic crash recovery around one journal.

### C. Sign-only with an internal body MAC

An internal MAC between signer and gateway detects substitution if the gateway is trusted to verify it. A compromised gateway can still ignore the MAC, so it is weaker than making transmission part of the key boundary while adding key distribution and replay complexity.

## Decision

Choose **sign-and-send**. The exact M13-authorized canonical bytes are constructed once inside a typed `ProductionRequestEnvelope`. The isolated security boundary independently revalidates the envelope, consumes a one-use internal claim, generates a fresh timestamp, signs only an allowlisted operation, and gives those exact bytes to a no-redirect, TLS-verifying, fixed-origin transport. Raw signatures and authentication headers never cross the boundary.

## Security and operational consequences

- Body, price, quantity, ticker, side, expiry, group, query, and subaccount substitution fail before transport.
- Only create, cancel, amend, and decrease typed operations exist; batch create, RFQ, key management, and arbitrary signing are absent.
- The future private key exists only in the signer boundary, initially from a mounted/encrypted secret; KMS/HSM implementations may replace the in-memory RSA backend without changing envelopes.
- Signer egress must be restricted to `external-api.kalshi.com:443`. TLS verification is mandatory and redirects are forbidden.
- A durable journal precedes the irreversible boundary. An ambiguous outcome becomes reconciliation-required and is never retried blindly.
- M15 has no activation transition, credential, or real transport. Restart always yields DISARMED. Offline interoperability uses ephemeral synthetic keys and an explicitly fake sender.

## Data flow

```text
deterministic control plane -> signer_internal request claim
                            -> isolated sign-and-send boundary
                            -> fixed TLS egress: external-api.kalshi.com:443

browser / dashboard --------X signer_internal
LLM / research / sources ---X signer_internal
M14 demo signer ------------X production credential domain
```
