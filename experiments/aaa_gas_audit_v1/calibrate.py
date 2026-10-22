#!/usr/bin/env python3
"""AAA daily-change descriptive statistics and leakage-resistant walk-forward baseline."""
import argparse
import csv
import json
import math
from datetime import date
from decimal import Decimal
from pathlib import Path

def load(path):
    rows = []
    with Path(path).open(newline="") as fh:
        for r in csv.DictReader(fh):
            if not r.get("date") or not r.get("national_regular_usd"):
                raise ValueError("missing date or price")
            if not r.get("source_sha256") or not r.get("acquired_at_utc") or not r.get("source_url"):
                raise ValueError("missing provenance; no calibration")
            value = Decimal(r["national_regular_usd"])
            if not value.is_finite() or value <= 0:
                raise ValueError("invalid price")
            rows.append((date.fromisoformat(r["date"]), value, r))
    if len({r[0] for r in rows}) != len(rows):
        raise ValueError("duplicate dates")
    rows.sort()
    if any((b[0]-a[0]).days != 1 for a,b in zip(rows, rows[1:])):
        raise ValueError("nonconsecutive daily observations; cannot infer daily changes")
    return rows

def analyze(rows, min_train=90, jump=Decimal("0.0100")):
    changes = [rows[i][1] - rows[i-1][1] for i in range(1, len(rows))]
    if len(changes) <= min_train:
        return {"status": "INSUFFICIENT_HISTORY", "daily_changes": len(changes),
                "minimum_training_days": min_train, "production_authority": False}
    # At each test index, use only earlier observed changes; no same-day leakage.
    predictions = []
    for i in range(min_train, len(changes)):
        train = changes[:i]
        p = (sum(x >= jump for x in train) + 1) / (len(train) + 2)
        y = int(changes[i] >= jump)
        predictions.append({"date": rows[i+1][0].isoformat(), "prob_jump": p, "actual_jump": y})
    brier = sum((p["prob_jump"] - p["actual_jump"])**2 for p in predictions)/len(predictions)
    logloss = -sum(p["actual_jump"]*math.log(p["prob_jump"]) +
                   (1-p["actual_jump"])*math.log(1-p["prob_jump"]) for p in predictions)/len(predictions)
    baseline_p = sum(p["actual_jump"] for p in predictions)/len(predictions)
    baseline_brier = sum((baseline_p-p["actual_jump"])**2 for p in predictions)/len(predictions)
    return {"status": "DESCRIPTIVE_RESEARCH_ONLY", "daily_changes": len(changes),
            "jump_threshold_usd": str(jump), "walk_forward_test_days": len(predictions),
            "brier": brier, "log_loss": logloss,
            "retrospective_constant_baseline_brier": baseline_brier,
            "predictions": predictions, "production_authority": False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--min-train", type=int, default=90)
    parser.add_argument("--jump", default="0.0100")
    args = parser.parse_args()
    print(json.dumps(analyze(load(args.csv_path), args.min_train, Decimal(args.jump)), indent=2))
