# Assumptions

- Python 3.12 is the minimum runtime; newer compatible runtimes are acceptable in development.
- Services begin as modules in one distribution and may be split into processes without changing trust boundaries.
- All integrations remain fixture/offline until credentials and external acceptance are performed by the owner.
- Production writes and bounded autonomy remain disabled regardless of environment variables in general services.

## M1 offline account adapter

Until current official Kalshi specifications can be fetched, the M1 adapter uses the endpoint and
authentication starting references in `MASTER_SPEC.md`. This assumption is not approved for live
use: API drift and key scope must be verified first, and all production writes remain unavailable.

## Current fixed-point quantities correction

As externally verified for April 17, 2026 behavior, current `_fp` contract quantities accept 0–2 decimals
with 0.01 minimum granularity. `fractional_trading_enabled` is deprecated compatibility payload data, is not
canonical eligibility state, and `fractional_trading_updated` is not expected. Historical raw payloads retain
the compatibility field.
