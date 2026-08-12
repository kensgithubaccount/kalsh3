# Oracle deployment hardening

For provider-neutral Ubuntu prerequisites (including the mandatory Redis sysctl) and the AWS EC2 path,
start with [`../deployment.md`](../deployment.md). The controls below supplement that baseline.

- Run on a patched minimal Oracle host with firewall ingress limited to SSH administration and Caddy
  ports 80/443. Caddy owns certificates; the dashboard is never published directly.
- Compose services use bounded logs, restart policies, health checks, dropped capabilities, no-new-
  privileges, read-only filesystems where practical, private data/event/signer networks, resource ceilings,
  and named persistent volumes. The signer has no public port and no web/data/event network.
- Mount database and backup secrets as files. Never place production-write PEM in Compose, environment,
  shell history, Git, logs, support exports, or ordinary state storage. Future signer secret material uses
  a root-readable mounted secret or vault and is never persisted decrypted.
- Monitor boot-volume and volume utilization, inode availability, container restarts, log growth, backup
  age, restore-drill age, queue depth, clock synchronization, and certificate expiry. Halt new risk before
  exhaustion. Docker log rotation is capped at three 10 MiB files per container.
- Pin and review image digests before live deployment, scan images/SBOM, stage migrations, back up first,
  deploy read-only, verify health/reconciliation, and retain the prior image/config for rollback.
- Rollback never rolls database schema backward destructively. Restore compatible application code, run
  read-only smoke tests, reconcile, and leave production `DISARMED` pending a new human workflow.

The checked Compose topology and the first AWS Redis/NATS startup evidence do not verify Oracle
firewalling, TLS issuance, resource behavior, object storage, backups, restore drills, or runtime
isolation. Those remain **NOT VERIFIED** until exercised on the target.
