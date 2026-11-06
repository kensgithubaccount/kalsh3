# PR268 exception diagnosis — offline research only

This additive diagnostic does not repair or replace V3, alter its budgets, skip tests,
collect forecasts, contact Kalshi, or confer scientific or trading authority.

The pytest plugin records original exceptions at the parent probe frame and cached
child state before close. It does not poll or wait on a process. It stores bounded
primitive records in memory and opens its create-once output only after tests.
A transient /proc FD directory read and tracing overhead remain observer effects;
untraced controls are required. Cached exit code before close plus the returned
reaping flag are evidence; absence of an exception record is not proof of success.
Tracing covers the main test thread only. Threaded duplicate-invocation probes are
tested normally but are not covered by this exception observer. Descriptor scans
may include their own transient directory handle; the actual incoming FD is logged.

All test processes in the diagnostic workflow run with no external network in a
Linux network namespace, retaining the runner UID. Dependency setup and artifact
transfer occur outside that namespace. Loopback is enabled only for local tests.
The workflow records the actual runner image rather than assuming a pinned image.
Only a push to the separate diagnostic branch triggers it. No diagnostic PR is
opened, leaving the original PR workflow and both reviewed draft PRs untouched.
This diagnostic completion is not a four-job CI PASS or a merge recommendation.

Run isolated untraced and traced V3 first, then the full unmodified suite in fresh
Python processes for reviewed PR268, canonical base and last-passing PR267.
The full-suite comparison includes changed test collection/order. Failures are
retained as results, not silently retried. Generated diagnostics are uploaded after
all comparisons. No original source/evidence is rewritten.

Local observer test:
`python -m pytest -q diagnostics/pr268/test_observer.py`

A root cause requires the actual exception/operation and discriminating controls.
A runner-image correlation alone is insufficient. Any proposed source repair needs
separate approval, regressions, full CI and exact-head independent review.

