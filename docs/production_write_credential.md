# Future production write credential procedure

M15 does not install or request a production write credential. The future credential must be distinct from the production-read and demo-write domains, explicitly validate `read` and `write` scopes against production account 0, and enter only the isolated signer through a mounted secret or encrypted vault. General environment variables and the dashboard state database are not approved secret stores.

Enrollment requires authenticated owner governance, password and TOTP reauthentication, an explicit production warning, direct protected-backend PEM upload, environment/account/scope validation, sealed persistence, and a secret-free audit event. The PEM is never returned. M15 implements only an offline fixture validator; it is not connected to a route.

Rotation installs and validates a new sealed version inside the isolated boundary before retiring the prior version. A credential anomaly immediately kills signing. Removal deletes signer secret material and leaves production DISARMED. Revocation of the remote API credential is a human action in Kalshi's authenticated key-management interface; M15 does not call API-key deletion endpoints.

The signer interface permits a future KMS/HSM backend: it consumes typed envelopes and returns only normalized transport outcomes, so key custody can move without exposing a raw signing oracle.
