# Security Model

Threats include credential theft, malicious source content and prompt injection, confused-deputy writes, replay or duplicate orders, stale market state, compromised dependencies, account mismatch, and disclosure through logs/support exports.

Controls include isolated read/write keys, encrypted server-side credential storage, no browser persistence of PEM material, short-lived one-use authorizations, strict host/method/path/body validation, deterministic reconciliation, least-privilege containers, secret scanning, dependency scanning, redaction tests, secure sessions, TOTP, CSRF protection, CSP, HSTS, throttling, and audit records.

Production write configuration is deliberately absent from general service environment configuration.
