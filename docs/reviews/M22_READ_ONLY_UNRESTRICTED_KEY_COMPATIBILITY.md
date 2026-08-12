# M22 — Read-Only Unrestricted-Key Compatibility

Kalshi documents the API-key `subaccount` restriction as optional. An omitted or null value represents an
unrestricted key; integer `0` explicitly restricts the credential to the primary account; a positive value
restricts it to that subaccount. Enrollment now accepts only the first two primary-compatible cases after
uniquely matching `api_key_id` and requiring scopes exactly equal `["read"]`. Booleans, strings, malformed
values, nonzero restrictions, duplicate matches, and expanded scopes fail closed.

Credential breadth is separate from runtime targeting. Every production portfolio read continues to include
`subaccount=0`; no generic account/subaccount request surface was added, and authentication remains GET/HEAD
only. Production signer is **DISARMED**, production-write credential is **NONE**, bounded autonomy is **OFF**,
and real-money orders are **NONE**. Live deployment and production-read retry remain pending.
