# Threat Model

Protected assets are capital, credentials, account data, forecasts, audit evidence, and control state. External APIs, documents, social content, browsers, networks, dependencies, and LLM providers are untrusted. Primary failure modes are unauthorized or duplicate orders, silent accounting errors, future leakage, poisoned evidence, secret disclosure, and false operational confidence.

The platform fails closed, separates duties, validates at every boundary, versions source data, uses exact arithmetic, reconciles exchange state, and preserves immutable audit evidence.
