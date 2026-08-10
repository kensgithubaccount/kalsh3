# Production Activation

Production write credentials are unnecessary for research, forecasting, read-only monitoring, or the dashboard. Installing a write credential never arms trading. Activation requires owner authentication, password re-authentication, current TOTP, reviewed risk policy and promotion gates, explicit acknowledgement, separate HTTPS credential upload to the signer, and scope validation. Every restart disarms.

No developer, test, CI job, or automation may submit a real-money order.
