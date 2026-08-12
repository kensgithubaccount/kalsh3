# M22 — Read-Only Unrestricted-Key Compatibility Correction

## Finding and source status

The second live production-read setup attempt on the healthy AWS stack failed the API-key
enrollment check. `verify_exact_read_scope` positively required the returned API key's
`subaccount` field to equal the integer `0`. The operator's provisioned read-only key is an
unrestricted key, and Kalshi's current API-key contract does not stamp an unrestricted key with
`subaccount: 0`; it omits the field entirely. The correction below was reviewed against the same
official Kalshi API-key contract facts used for M21.

## Corrected acceptance rule

`GET /trade-api/v2/api_keys` enrollment continues to require exactly one matching `api_key_id`
and `scopes == ["read"]`. The `subaccount` field on the matched key is now accepted when it is:

- absent from the object entirely, or
- explicitly `null`, or
- the exact integer `0`.

Every other observed shape is rejected and fails closed, including nonzero integers (positive or
negative), booleans (`true`/`false`, even though Python's `bool` is an `int` subclass), strings
(including the literal `"0"`), arrays, objects, and floats. The check is implemented as a single
`_api_key_subaccount_is_compatible` helper in
`services/kalshi_account_gateway/client.py` so the accepted-shape logic has one authoritative
location instead of being inlined into the boolean guard.

No other part of the enrollment or read path changed. A key with ambiguous, duplicate, missing,
expanded, or write-capable scope still fails closed exactly as before.

## Runtime account targeting is unchanged

This correction only affects how the *credential's own* API-key metadata is verified during
enrollment. It does not introduce any way to read a non-primary account at runtime:

- `GET /trade-api/v2/portfolio/balance` is still requested with an explicit `subaccount=0` query
  parameter.
- Positions, orders, fills, and settlements are still requested with an explicit `subaccount=0`
  query parameter on every page, including follow-up cursor pages.
- The resulting `AccountSnapshot.subaccount` is still hardcoded to `0` in
  `services/kalshi_account_gateway/models.py` and is not derived from the API key's own
  `subaccount` field.
- There is still no generic subaccount parameter, request method, or mutation interface anywhere
  in `KalshiAccountClient`; only the fixed GET/HEAD read surface listed in M21 exists.

## Safety posture

Production signer remains **DISARMED**; production-write credential **NONE**; bounded autonomy
**OFF**; account reads remain pinned to subaccount **0** at request time regardless of the
enrolled key's own subaccount metadata; gateway remains **GET/HEAD only**; real-money orders
**NONE**. No strategy, model, risk, authorization, credential-handling, or production-write
behavior was touched. Live production-read acceptance with this correction, and any further API
drift beyond the unrestricted-key shape identified here, remain PENDING.

## Regression coverage

`tests/test_account_gateway.py` adds focused parametrized coverage:

- `test_read_key_missing_subaccount_key_entirely_is_accepted` — an unrestricted key that omits
  `subaccount` enrolls successfully.
- `test_read_key_with_null_or_zero_subaccount_is_accepted` — explicit `null` and explicit `0`
  both enroll successfully and the resulting snapshot still targets `subaccount == 0`.
- `test_read_key_with_incompatible_subaccount_is_rejected` — nonzero integers, negative integers,
  both booleans, `"0"` and other strings, and array/object shapes all fail closed with
  `AuthenticationRejected`.

The prior scope-exactness, unambiguous-match, and current-key-id regression tests are unchanged.
