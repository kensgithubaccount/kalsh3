# M20 — Live Deployment Corrections review

## Scope and live evidence

The first real Docker deployment used AWS EC2 Ubuntu 24.04 x86_64. Redis repeatedly exited 127 with
`setpriv: setresuid failed: Operation not permitted`: its root entrypoint could not change users after
Compose dropped every capability. A temporary override running the container as `redis` retained
`cap_drop: [ALL]` and `no-new-privileges:true`; Redis then became healthy and reported
`Ready to accept connections tcp`. The host also emitted Redis's `vm.overcommit_memory=1` warning; the
operator is validating the new persistent sysctl prerequisite on that host.

The same deployment exposed that Caddy's `{$KPV3_HOSTNAME}` was not passed into its container. A temporary
override validated the missing wiring. It is now explicit and fail-fast in the effective Compose config.

Finally, the NATS healthcheck invoked `nats-server --signal ldm`. That command requests lame-duck/graceful
shutdown and therefore made health checking destructive. NATS monitoring is now enabled only on its
private network and `/healthz` is probed without signals. CI starts the hardened Redis and NATS services,
runs the NATS probe repeatedly, checks restart count/running state, and performs a client publish.

## Safety review

- Redis receives no added capability and remains on the internal data network.
- NATS monitoring has no host port and remains on the internal events network.
- Only Caddy publishes 80/443. The signer remains isolated and explicitly `DISARMED`.
- No production-write credential, model, strategy, authorization, autonomy, or risk limit changed.
- The runtime smoke starts only Redis/NATS and creates no credentials or secret files.

## Remaining evidence

Full AWS firewall/host-hardening inspection, public DNS/TLS, reboot persistence of the sysctl, PostgreSQL,
application services, signer runtime isolation, production reads/reconciliation, backups and restore drill,
alerts, long-duration restart/resource behavior, and Oracle deployment remain **NOT VERIFIED / PENDING**.
No production order was placed and full production readiness is not claimed.
