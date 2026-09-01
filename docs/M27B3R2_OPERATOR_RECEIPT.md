# M27B.3R2 smoke receipt

Use `scripts/run_m27b3_smoke_receipt.py` from the repository root for future
bounded smokes. It waits for the child without imposing a timeout, runs Python
with `-u` and `PYTHONUNBUFFERED=1`, and writes separate `stdout.log` and
`stderr.log` files plus `process-receipt.json` in the supplied unique run
directory.

The receipt records the child PID, requested and effective command, code SHA,
UTC start/end, exit code or terminating signal, child resource usage, output
hashes, and hashes for `universe.sqlite` and `observations.sqlite` when those
files exist. The wrapper does not select a host, add headers, inspect response
bodies, or change application authority.

Example:

```text
/Users/ksyme/miniforge3/envs/kalsh3/bin/python -u scripts/run_m27b3_smoke_receipt.py \
  --run-dir /path/to/unique-run \
  --code-sha e8c6faff5a72db6010fd4ae22713b0a0831b947e \
  -- /Users/ksyme/miniforge3/envs/kalsh3/bin/python -m \
  services.opportunity_engine.structural_measurement_runner \
  --archive /path/to/unique-run/universe.sqlite \
  --evidence-db /path/to/unique-run/observations.sqlite \
  --live-public-read --cadence-seconds 900 --max-iterations 1 \
  --source-authority external-api.kalshi.com
```
