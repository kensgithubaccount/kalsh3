# Deployment

The target is Oracle Cloud Ubuntu behind Caddy. Run `deploy/oracle/install.sh` once as the service owner;
it is idempotent and never replaces existing vault, database, or setup secrets. Configure `KPV3_HOSTNAME`
and start Compose. Only ports 80/443 (and operator-restricted SSH) are public. App port 8000 and data
services remain on private networks. Caddy owns durable certificate volumes and supplies HTTPS/HSTS.

Back up the encrypted app-state volume and PostgreSQL data using protected storage; back up the vault key
separately. A backup containing only one side must not disclose credentials. Restore validation and an
actual Docker start remain human acceptance checks in the current Docker-less environment.
