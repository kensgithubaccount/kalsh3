# Operations Runbook

Start in offline or shadow mode, verify dependencies and migrations, then check database, event transport, cache, source freshness, clock, reconciliation, and global state. Any unknown write outcome, mismatch, stale book, source failure near settlement, monitoring failure, or clock drift blocks new risk.

Global halt is durable: prohibit new risk, cancel resting orders where possible, reconcile, disable signer authorization, alert the owner, and require authenticated human reset.
