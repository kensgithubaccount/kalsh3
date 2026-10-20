# CPI-E1-P11 Terminal Postmortem - 2026-10-14

## Canonical terminal status

**CPI-E1-P11: CLOSED / INCONCLUSIVE_INSUFFICIENT / NO-BACKFILL / ARCHIVED / VM RETIRED**

Frozen protocol SHA-256:
`e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d`

GCE runtime head:
`b601c21a3fa9ff46ceb006b5a653ba71eaa537ec`

## Failure sequence

- 2026-10-14 11:45Z preflight: `TRANSPORT_BLOCK`, error `BLS schedule response rejected`.
- 2026-10-14 12:05Z market stage: the same BLS preflight dependency returned the same
  `TRANSPORT_BLOCK`; no market capture artifact was issued.
- 2026-10-14 12:20:01.235185Z preclose: terminal
  `INCONCLUSIVE_INSUFFICIENT`, reason `MISSED_OR_INCOMPLETE_PRE_RELEASE_COLLECTION`.
- `no_backfill=true`, `promotion_authority=NONE`, `phase1_economic_test_authorized=false`,
  `production_influence=0`.

No Reuters coverage artifact was admitted because the frozen first-party preflight never
completed. No post-cutoff repair is admissible.

## Sealed scientific root

The terminal scientific root contained only:

- `protocol.json`
- `failure.json`
- `manifest.sha256`

Manifest entries:

```text
72e1c1673a1179b22e6caef57b0b9efc266915540f0bd1b97671718e71c7dae1  failure.json
6629e715989fb3ca736e40b7eaedb58d4fdbaa7600b2ea8caec0c726423bf9e1  protocol.json
```

The frozen runtime's terminal manifest verifier returned `PASS` both before and after scheduler
cleanup.

## Archive and retirement

Final post-cleanup archive:
`cpi_p11_20261014_final_postcleanup.tar.gz`

Archive SHA-256:
`5e4a2bc97b5078d91f38d0b8ad4b3b3ea3f64fd81581939bfeb3ddf9965d745a`

Durable object:
`gs://total-market-138523-kalshi-evidence/cpi_p11_20261014_final_postcleanup.tar.gz`

The Cloud Storage bytes were streamed back and reproduced the exact archive SHA-256 before VM
retirement. All P11 systemd timers were removed, the scientific root still verified, deletion
protection was removed, and the dedicated P11 VM was deleted.

## Scientific interpretation

P11 contributes **zero** directional evidence. It is neither prospective support nor a
prospective contradiction. Earlier historical Reuters-vs-Kalshi diagnostics are unchanged,
but P11 does not promote them. The failure is operational evidence that a single release-day
BLS transport dependency is not sufficient for a prospective collector.

The follow-on reliability work is documented in `CPI_P12_RELIABILITY_REPAIR.md`.
