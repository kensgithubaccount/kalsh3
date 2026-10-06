#!/usr/bin/env python3
"""Operator CLI for the frozen CPI-E1-P11 prospective runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.forecasting.cpi_p11_runtime import (
    P11AuthorityError,
    P11OperationalError,
    P11TimingError,
    P11TransportError,
    RunPaths,
    capture_market_evidence,
    close_pre_release,
    collect_bls_truth,
    record_reuters_pass,
    run_preflight,
    score_and_seal,
    verify_terminal_manifest,
)

DEFAULT_ROOT = Path.home() / "cpi_e1_p11_prospective_20261014"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen CPI-E1-P11 prospective operator")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight")
    sub.add_parser("market")
    reuters = sub.add_parser("record-reuters-pass")
    reuters.add_argument("--input", required=True, type=Path)
    sub.add_parser("preclose")
    truth = sub.add_parser("truth")
    truth.add_argument("--score", action="store_true")
    sub.add_parser("score")
    sub.add_parser("verify")
    return parser


def main() -> int:
    args = _parser().parse_args()
    paths = RunPaths(args.root)

    try:
        if args.command == "preflight":
            payload = run_preflight(paths)
        elif args.command == "market":
            payload = capture_market_evidence(paths)
        elif args.command == "record-reuters-pass":
            candidate = json.loads(args.input.read_bytes())
            if not isinstance(candidate, dict):
                raise P11AuthorityError("Reuters input must be a JSON object")
            payload = record_reuters_pass(paths, candidate)
        elif args.command == "preclose":
            payload = close_pre_release(paths)
        elif args.command == "truth":
            payload = collect_bls_truth(paths)
            if args.score:
                payload = score_and_seal(paths)
        elif args.command == "score":
            payload = score_and_seal(paths)
        elif args.command == "verify":
            verify_terminal_manifest(paths)
            payload = {"status": "PASS", "manifest": str(paths.manifest)}
        else:  # pragma: no cover - argparse owns command set
            raise AssertionError(args.command)
    except P11TimingError as exc:
        print(json.dumps({"status": "TIMING_BLOCK", "error": str(exc)}, sort_keys=True))
        return 3
    except P11TransportError as exc:
        print(json.dumps({"status": "TRANSPORT_BLOCK", "error": str(exc)}, sort_keys=True))
        return 4
    except P11OperationalError as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": str(exc)}, sort_keys=True))
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "FAILED_CLOSED", "error": type(exc).__name__}, sort_keys=True))
        return 2

    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
