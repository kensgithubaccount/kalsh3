# Threat Model

Protected assets are capital, credentials, account data, forecasts, audit evidence, and control state. External APIs, documents, social content, browsers, networks, dependencies, and LLM providers are untrusted. Primary failure modes are unauthorized or duplicate orders, silent accounting errors, future leakage, poisoned evidence, secret disclosure, and false operational confidence.

The platform fails closed, separates duties, validates at every boundary, versions source data, uses exact arithmetic, reconciles exchange state, and preserves immutable audit evidence.

## M1 credential and dashboard threats

M1 mitigates default-full-scope keys by accepting only a positively verified exact `read` scope. Credentials
are encrypted with encrypt-then-MAC storage, the master key is separate, sessions are hashed at rest, cookies
are Secure/HttpOnly/SameSite, state-changing browser requests require CSRF tokens, login is throttled, and
security headers deny framing and cross-origin content. Remaining deployment risks include host compromise,
backup co-location, proxy bypass, and failure to preserve keys across upgrades; the target deployment keeps
port 8000 private and separates protected backups.

## M5 hostile external content

External events are hostile data. Controls include exact source/host allowlists, HTTPS, SSRF/private-address
rejection, disabled redirects, bounded bytes/time/content types, malformed-input rejection, output escaping,
repr-hidden credentials, bounded queues with visible gaps, Unicode normalization, conservative deduplication,
and no rendering/execution of raw payloads. Reposts do not become independent corroboration. Screenshot-only,
old-news, vendor-only, deleted, conflicting and suspicious timestamp claims receive descriptive risk flags,
never accusations or trade authority.
