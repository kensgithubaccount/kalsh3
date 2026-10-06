# CPI-E1-P11 — Operational Runbook

## Scope

This runbook operationalizes the already-frozen CPI-E1-P11 prospective protocol.

It does **not** change:
- target event;
- market observation rule;
- Reuters authority/admission rule;
- common denominator;
- scoring;
- result classes;
- promotion authority;
- economics/trading authority.

Frozen protocol SHA-256:

`e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d`

Target:
- `KXCPI-26SEP`
- September 2026 CPI
- Kalshi close: 2026-10-14 08:25 EDT / 12:25Z
- BLS release: 2026-10-14 08:30 EDT / 12:30Z

Production influence remains exactly zero.

## Runtime architecture

The operator CLI is:

`scripts/run_cpi_e1_p11.py`

The implementation is:

`services/forecasting/cpi_p11_runtime.py`

The scientific evidence root is exactly:

`~/cpi_e1_p11_prospective_20261014`

Scheduler state/logs are deliberately outside that root:

`~/cpi_p11_scheduler_20261014`

This prevents launchd log appends from invalidating the terminal scientific
`manifest.sha256`.

The arming script creates a detached git worktree at the exact reviewed runtime
commit and creates:

`~/run_cpi_p11_frozen.sh`

Every scheduled collection command and Reuters receipt import uses that pinned
runner.

## Live-vs-historical candlestick endpoint

The frozen rule is unchanged: use the unique latest **60-minute** candlestick
whose `end_period_ts` is strictly before the sibling market close, and use
YES ask as the primary market price.

Kalshi's current API routes unarchived markets through:

`/trade-api/v2/series/{series_ticker}/markets/{ticker}/candlesticks`

and archived markets through the historical endpoint. P11 therefore uses the
live current-market route while `KXCPI-26SEP` is open.

Current live candlesticks may expose cent-valued `close` and dollar-valued
`close_dollars`. The runtime retains the exact raw response bytes and
deterministically normalizes the parser view to unit-dollar Decimal values.
When both representations are present they must agree exactly or the run fails
closed.

This is an API representation adapter only. It does not change the frozen
snapshot time, price side, threshold, or scoring rule.

## Oct. 14 schedule — America/New_York

The LaunchAgents are local-calendar jobs and the arming script refuses to arm
unless the Mac is currently on EDT (`-0400`).

| Local | UTC | Stage |
|---|---|---|
| 07:30 | 11:30Z | start six-hour `caffeinate -i -s` guard |
| 07:45 | 11:45Z | first-party BLS/Kalshi preflight |
| 08:05 | 12:05Z | capture complete 14-sibling market/candle evidence |
| before 08:20 | before 12:20Z | record Reuters PASS/UNKNOWN/FAILURE receipt |
| 08:20 | 12:20Z | pre-release watchdog / hard no-backfill cutoff |
| 08:35, 08:45, 09:00, 10:00, 12:00 | post-release | retry BLS initial-release acquisition and score if available |

BLS truth retry after release is allowed because it cannot repair missing
pre-release evidence and does not alter the prospective predictor/market
capture.

## Preflight

The runtime stores exact bounded raw evidence envelopes for:
- BLS CPI release calendar;
- Kalshi KXCPI series;
- Kalshi `KXCPI-26SEP` event;
- complete event market inventory.

It fails closed unless:
- target series/event identities match exactly;
- BLS is still the settlement authority;
- exactly 14 siblings exist;
- every sibling is active, binary, non-provisional, non-MVE;
- every sibling resolves strict-GT September 2026 headline CPI MoM;
- every sibling closes at exactly 12:25Z.

The exact ticker/threshold/close signature is frozen into `preflight.json`.
Market capture must reproduce it exactly.

## Market capture

Market capture is admitted only in:

`[2026-10-14T12:05:00Z, 2026-10-14T12:20:00Z)`

For each of all 14 siblings:
1. fetch exact bounded raw candlestick bytes;
2. normalize only the current API price representation;
3. run the reviewed P9A candle validator;
4. select latest 60-minute candle strictly before close;
5. preserve YES ask or explicit missing/boundary reason;
6. persist create-once evidence.

No threshold subset may be selected.

## Reuters evidence — reviewed manual/assistant boundary

The runtime intentionally does **not** contain a generic Reuters scraper.

The reviewed P10B/P10C process requires content-level judgments:
Reuters attribution, exact reference month in body text, prospective headline
MoM semantics, revision identity, publication timing, and independent-host
corroboration.

Before 08:20 EDT, the researcher/assistant completes the frozen three-rung
search procedure and then uses exactly one template under:

`docs/reviews/artifacts/cpi-p11-runtime-templates/`

### PASS

Populate `reuters-pass-template.json` without storing full wire text.

Required:
- exact Decimal forecast;
- Reuters attribution;
- September 2026 proven in article body/dateline;
- prospective headline CPI MoM;
- no retrospective language;
- same load-bearing forecast-sentence SHA-256 across at least two independently
  operated approved hosts;
- each retrieval before 12:20Z;
- each publication before the 12:25Z market close;
- exact raw-response SHA-256 for each host.

Record with the pinned runner:

```bash
~/run_cpi_p11_frozen.sh record-reuters-pass /ABSOLUTE/PATH/reuters-pass.json
```

### UNKNOWN — searched, no qualifying observation

UNKNOWN is permitted only if **all three frozen search rungs actually
completed**.

Populate `reuters-unknown-template.json`, then:

```bash
~/run_cpi_p11_frozen.sh record-reuters-nonpass /ABSOLUTE/PATH/reuters-unknown.json
```

The runtime refuses UNKNOWN unless the receipt positively says the full search
ladder completed.

### FAILURE

If acquisition/authority could not be completed, use the failure template:

```bash
~/run_cpi_p11_frozen.sh record-reuters-nonpass /ABSOLUTE/PATH/reuters-failure.json
```

If **no Reuters terminal receipt exists by 12:20Z**, the watchdog records
`FAILURE_ACQUISITION_OR_AUTHORITY`. It never fabricates
`UNKNOWN_SEARCHED_NO_QUALIFYING_OBSERVATION`.

## BLS truth and scoring

After 12:30Z the runtime uses the existing reviewed BLS acquisition → evidence
issuance → P6 initial-release parser chain.

It accepts only September 2026 BLS initial-release headline all-items
seasonally adjusted month-over-month truth.

If Reuters is not PASS, the final scientific decision is
`INCONCLUSIVE_INSUFFICIENT`.

If Reuters is PASS:
- both Reuters and Kalshi are scored on the same eligible siblings;
- missing/boundary YES asks are excluded;
- exact Kalshi 0.5 is a tie and excluded;
- truth is YES iff BLS initial-release value > threshold;
- Reuters/Kalshi correctness counts feed the frozen decision function.

Every result still carries:
- promotion authority `NONE`;
- Phase-1 economics unauthorized;
- production influence `0`.

## Terminal integrity

Terminal `result.json` and `failure.json` are mutually exclusive.

After terminal issuance the runtime creates `manifest.sha256` over every
scientific artifact then present.

Verify with:

```bash
~/run_cpi_p11_frozen.sh verify
```

The scheduled runner does not expose `verify`; use the pinned worktree CLI or
the final audit command after the campaign. Do not edit any terminal artifact.

## Arming

After the operational PR is merged, on the Mac:

```bash
cd ~/kalsh3
git fetch origin
git switch main
git pull --ff-only
zsh scripts/arm_cpi_e1_p11_launchd.sh
```

Arming requires:
- local `main == origin/main`;
- clean tracked worktree;
- `uv` on PATH;
- current EDT offset `-0400`.

The script writes an arming receipt containing:
- exact runtime commit/tree;
- exact protocol SHA;
- pinned runner SHA;
- every LaunchAgent plist SHA;
- timezone/offset.

## Mac power requirement

Launchd does not make a sleeping closed-lid Mac execute normal user jobs.

For the Oct. 14 run:
- keep the lid open;
- keep the Mac plugged in;
- keep it logged in;
- do not change system timezone away from America/New_York.

The 07:30 caffeinate job helps prevent **idle** sleep once it has launched; it
cannot wake a Mac that is already fully asleep at 07:30.

## Cleanup

The LaunchAgents use Month/Day without Year and therefore would recur annually
if left installed.

After terminal verification:

```bash
cd ~/kalsh3
zsh scripts/cleanup_cpi_e1_p11_launchd.sh
```

Cleanup archives scheduler/plist state and removes the agents. It does not
modify the scientific evidence root.
