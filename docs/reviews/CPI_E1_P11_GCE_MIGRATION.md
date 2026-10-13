# CPI-E1-P11 — GCE migration runbook

## Purpose

This runbook moves the already-frozen CPI-E1-P11 operational runtime from the
owner's Mac to an always-on Google Compute Engine VM because the Mac cannot
remain open through the critical Oct. 14 pre-release window.

No scientific rule changes.

Frozen protocol SHA-256 remains:

`e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d`

The VM runs the same reviewed runtime and the same scientific evidence root
basename:

`cpi_e1_p11_prospective_20261014`

## Architecture

The recommended host is a standard, non-preemptible Compute Engine VM:

- Ubuntu 24.04 LTS;
- `e2-small` by default;
- standard provisioning model;
- live migration maintenance policy;
- automatic restart enabled;
- Shielded VM Secure Boot;
- deletion protection enabled until the experiment is archived.

The default zone is `us-east4-b`. Override it with
`CPI_P11_GCP_ZONE` if the project uses a different supported zone.

No authenticated Kalshi access or trading credential is installed. P11 uses
public first-party reads only.

## Why systemd timers

Linux `systemd` timers replace the Mac LaunchAgents.

All pre-release timers use exact UTC timestamps and:

`Persistent=false`

This is important. If the VM is unavailable across a scheduled pre-release
time, systemd must **not** replay that missed stage later. The runtime itself
also retains its frozen timing guards, so late execution still fails closed.

Scientific evidence remains under:

`~/cpi_e1_p11_prospective_20261014`

Scheduler state and logs remain outside it:

`~/cpi_p11_vm_scheduler_20261014`

## Remote schedule

| UTC | EDT | Stage |
|---|---|---|
| 11:45Z | 07:45 | first-party BLS/Kalshi preflight |
| 12:05Z | 08:05 | 14-sibling Kalshi market/candle capture |
| 12:20Z | 08:20 | hard pre-release closeout/no-backfill cutoff |
| 12:35Z | 08:35 | BLS truth/score attempt 1 |
| 12:45Z | 08:45 | BLS truth/score attempt 2 |
| 13:00Z | 09:00 | BLS truth/score attempt 3 |
| 14:00Z | 10:00 | BLS truth/score attempt 4 |
| 16:00Z | 12:00 | BLS truth/score attempt 5 |

The VM clock must be NTP-synchronized before arming.

## Provisioning

After the GCE migration PR is merged, update the canonical local checkout, then
run:

```bash
cd ~/kalsh3_p11_canonical
git fetch origin
git switch main
git pull --ff-only

bash scripts/provision_cpi_e1_p11_gce.sh
```

The script uses the currently configured `gcloud` project. To override:

```bash
export CPI_P11_GCP_PROJECT="YOUR_PROJECT_ID"
export CPI_P11_GCP_ZONE="us-east4-b"
export CPI_P11_GCP_INSTANCE="kalsh3-cpi-p11-20261014"
bash scripts/provision_cpi_e1_p11_gce.sh
```

The provisioner:

1. requires canonical local `main == origin/main`;
2. enables the Compute Engine API;
3. refuses to reuse an existing VM with the target name;
4. creates a standard Ubuntu 24.04 VM with deletion protection;
5. waits for SSH;
6. clones the public canonical repository;
7. verifies the VM checkout equals the exact local runtime head;
8. installs pinned `uv==0.12.23` into an isolated venv;
9. arms the reviewed Linux/systemd runtime;
10. prints the remote arming receipt, timers, and VM identity.

Do not remove the Mac LaunchAgents until the remote arming output has been
reviewed and accepted.

## Reuters evidence when the Mac is closed

The Reuters evidence step remains manually reviewed and must finish before
12:20Z / 08:20 EDT.

It can be submitted from **any machine with Google Cloud CLI**, including
Google Cloud Shell.

After preparing the reviewed PASS or non-PASS JSON receipt, run:

```bash
export CPI_P11_GCP_PROJECT="YOUR_PROJECT_ID"
export CPI_P11_GCP_ZONE="us-east4-b"

bash scripts/submit_cpi_e1_p11_reuters_gce.sh \
  pass \
  /path/to/reuters-pass.json
```

or:

```bash
bash scripts/submit_cpi_e1_p11_reuters_gce.sh \
  nonpass \
  /path/to/reuters-nonpass.json
```

The helper copies only that receipt to `/tmp` on the VM, then invokes the
same pinned remote runner. The runtime still enforces the 12:20Z cutoff and all
reviewed Reuters authority checks.

If no Reuters terminal receipt exists by 12:20Z, the remote watchdog records
`FAILURE_ACQUISITION_OR_AUTHORITY`. It does not reconstruct a prospective
PASS afterward.

## Mac handoff

Only after the VM arming receipt and timers are verified should the original
Mac LaunchAgents be removed:

```bash
cd ~/kalsh3_p11_canonical
zsh scripts/cleanup_cpi_e1_p11_launchd.sh
```

That avoids two authoritative collectors.

## After the experiment

First verify the remote terminal manifest:

```bash
gcloud compute ssh kalsh3-cpi-p11-20261014 \
  --project="YOUR_PROJECT_ID" \
  --zone="us-east4-b" \
  --command='~/run_cpi_p11_frozen_vm.sh verify'
```

Then copy the immutable evidence root back:

```bash
gcloud compute scp --recurse \
  kalsh3-cpi-p11-20261014:~/cpi_e1_p11_prospective_20261014 \
  ~/cpi_e1_p11_prospective_20261014_from_gce \
  --project="YOUR_PROJECT_ID" \
  --zone="us-east4-b"
```

After hashes are reconciled, clean the VM timers:

```bash
gcloud compute ssh kalsh3-cpi-p11-20261014 \
  --project="YOUR_PROJECT_ID" \
  --zone="us-east4-b" \
  --command='cd ~/kalsh3_p11_cloud && bash scripts/cleanup_cpi_e1_p11_systemd.sh'
```

Only after evidence recovery and review should deletion protection be disabled
and the VM deleted.

## Scientific authority

The migration changes hosting only.

It does not authorize:
- protocol edits;
- backfill;
- retrospective Reuters substitution;
- fees/P&L;
- sizing;
- fills;
- orders;
- production influence.

Even `PROSPECTIVE_SUPPORT_DIAGNOSTIC` retains promotion authority NONE.
