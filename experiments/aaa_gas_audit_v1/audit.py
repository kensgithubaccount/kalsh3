#!/usr/bin/env python3
"""Fail-closed, standard-library-only AAA gasoline prediction ledger audit."""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED = ("record_id", "market_ticker", "side", "threshold",
            "observation_date", "recommendation_date", "production_influence",
            "research_only")

def validate(record):
    missing = [k for k in REQUIRED if k not in record]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))
    if record["side"] not in ("YES", "NO"):
        raise ValueError("invalid side")
    if record["production_influence"] is not False or record["research_only"] is not True:
        raise ValueError("research-only gate violated")
    try:
        Decimal(record["threshold"])
        observation = datetime.fromisoformat(record["observation_date"]).date()
        recommendation = datetime.fromisoformat(record["recommendation_date"]).date()
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise ValueError("invalid date or threshold") from exc
    if recommendation > observation:
        raise ValueError("retrospective recommendation")
    if record.get("fill_status") == "filled" and not record.get("fill_receipt"):
        raise ValueError("filled without receipt")
    if record.get("settlement_value") is not None and not record.get("settlement_source_receipt"):
        raise ValueError("settlement without source receipt")
    if record.get("recommendation_at_utc"):
        dt = datetime.fromisoformat(record["recommendation_at_utc"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timestamp must have UTC offset")
        if dt.astimezone(timezone.utc).date() > observation:
            raise ValueError("post-observation forecast")
    return record

def evaluate(record):
    """Score only documented settlements; never score an unresolved recommendation."""
    if record.get("settlement_value") is None:
        return "UNRESOLVED"
    actual = Decimal(str(record["settlement_value"]))
    threshold = Decimal(record["threshold"])
    yes = actual > threshold
    return "WIN" if yes == (record["side"] == "YES") else "LOSS"

def audit(path):
    seen = set()
    rows = []
    for lineno, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = validate(json.loads(line))
        if row["record_id"] in seen:
            raise ValueError(f"duplicate record_id at line {lineno}")
        seen.add(row["record_id"])
        rows.append(row)
    results = [evaluate(row) for row in rows]
    return {"records": len(rows), "wins": results.count("WIN"),
            "losses": results.count("LOSS"), "unresolved": results.count("UNRESOLVED"),
            "win_rate": (results.count("WIN") / (results.count("WIN") + results.count("LOSS"))
                         if results.count("WIN") + results.count("LOSS") else None),
            "status": "REPAIR REQUIRED", "production_authority": False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", nargs="?", default=str(Path(__file__).with_name("predictions.jsonl")))
    args = parser.parse_args()
    print(json.dumps(audit(args.ledger), indent=2))
