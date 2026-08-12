# Ubuntu deployment

The supported host baseline is a patched Ubuntu 24.04 x86_64 VM. The same host procedure applies to
AWS EC2 and Oracle Cloud; provider controls supplement rather than replace the host firewall.

1. Create a DNS record for the instance and set `KPV3_HOSTNAME` to that public hostname. Compose now
   passes this non-secret value explicitly to Caddy. Do not put credentials in environment variables.
2. Run `sudo deploy/ubuntu/prepare-host.sh`. This idempotently persists
   `vm.overcommit_memory = 1` in `/etc/sysctl.d/60-kalshi-v3-redis.conf`, applies it immediately, and
   verifies the active value. This is required before Redis starts; do not grant Redis capabilities as
   a substitute.
3. Run `deploy/oracle/install.sh` as the service owner (the script name is retained for compatibility).
   It initializes local state and file-mounted secrets idempotently and never replaces existing secrets.
4. Validate with `docker compose config --quiet`, then start with `docker compose up -d`. Inspect
   `docker compose ps` and service logs. Every restart leaves the signer `DISARMED`; do not add a
   production-write credential during deployment validation.

## AWS EC2 minimum network and host hardening

- Use an Ubuntu 24.04 x86_64 instance with encrypted EBS volumes, IMDSv2 required, automatic security
  updates, time synchronization, and restricted administrative identities. Pin and review images before
  production use.
- In the EC2 security group and network ACL, allow inbound TCP 80/443 from intended users. Allow TCP 22
  only from the operator's fixed address or use AWS Systems Manager Session Manager with port 22 closed.
  Do not publish 8000, 4222, 5432, 6379, or 8222.
- Apply a default-deny host firewall consistent with those rules. Keep Compose `data`, `events`, and
  `signer_internal` networks internal. The signer has no public, web, data, event, or egress network.
- Protect backups separately from the instance, encrypt them, restrict IAM to least privilege, and escrow
  the vault key separately. Follow the [backup/restore](operations/backup_restore.md) and
  [incident-response](operations/incident_response.md) runbooks.

The original [Oracle hardening notes](operations/oracle_hardening.md) remain applicable to Oracle-specific
controls. TLS issuance, cloud firewall rules, backup/restore, runtime isolation, alert delivery, live
account reads, and production reconciliation require explicit validation on each host. This repository
does **not** claim full production readiness.
