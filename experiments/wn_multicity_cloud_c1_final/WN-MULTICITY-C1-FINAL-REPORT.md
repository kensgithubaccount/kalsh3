# WN-MULTICITY-D1A / C1-FINAL

**PASS — MULTICITY FIRST-EVENT AUTHORITY READY FOR ENABLEMENT**

Scheduler `wn-multicity-20261003-20261014` is PAUSED. The scientific job has never executed. No Oct 3 data acquired.

- Amendment: `experiments/wn_multicity_prospective_v1/pre_first_event_amendment_v1.json`,
  sha256 `b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000`. No pre-existing D1 file changed.
- Final image: `sha256:0ffe08df80315922d64f2313f160b053151744416ed03dacafe38fcc6e24bc08`
  (supersedes C1 `sha256:05e66dd2…c744`).
- Records: `final_results.json`, `kalshi_cloud_canary.json`, `independent_review_exact_digest.json`.

Dispositions: 1 depth → amended (A); 2 ingestion wording → no change; 3 delayed snapshot → amended (B);
4 decision_at bound → deferred; 5 market authority failure seals no forecast → no change; 6 early-wrapper bound → no change.

Valid city-day for analysis: `packet.json` present and `failure.json` absent. Per-market executability is in
`market.json` and `packet.json`; a delayed miss is `delayed_snapshot_failure.json` only.

Not proven: a live active event passing validation at 09:00Z (first exercised Oct 3).

Non-scientific leftovers (safe to remove after enablement; none is referenced by the scientific job or Scheduler):
Cloud Run jobs `wn-multicity-c1-canary`, `wn-multicity-c1-timing`; paused Scheduler `wn-multicity-c1-timing-canary`;
objects under `gs://wn-multicity-c1-354269857197-20261001/c1_canary/` (runtime account cannot delete them);
image tags `c1-20261001`, `c1-20261001-reviewed`. Do not remove tag/digest `c1-final-20261001`.

Enable (not executed):

    gcloud scheduler jobs resume wn-multicity-20261003-20261014 --location=us-central1 --project=total-market-138523
