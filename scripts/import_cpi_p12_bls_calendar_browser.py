#!/usr/bin/env python3
"""Import one browser-saved BLS CPI schedule HTML file under manual attestation."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

from services.forecasting.cpi_calendar_manual_acquisition import (
    OPERATOR_ATTESTATION,
    CPIBLSManualCalendarEvidence,
    attest_and_import_manual_bls_cpi_calendar,
    validate_cpi_bls_manual_calendar_evidence,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import browser-saved BLS CPI release-calendar HTML. "
            "Research-only; does not freeze P12."
        )
    )
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--reference-month", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--attest",
        action="store_true",
        help=(
            "Explicitly attest that --file was saved from the exact official BLS "
            "CPI release-calendar URL in a normal browser."
        ),
    )
    return parser


def _iso(value) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _receipt(evidence: CPIBLSManualCalendarEvidence) -> dict[str, object]:
    validate_cpi_bls_manual_calendar_evidence(evidence)
    return {
        "record_type": "CPI-P12-MANUAL-BLS-CALENDAR-RECEIPT-v1",
        "source_locator": evidence.source_locator,
        "acquisition_mode": evidence.acquisition_mode,
        "operator_attestation": evidence.operator_attestation,
        "imported_at_utc": _iso(evidence.imported_at),
        "reference_month": evidence.reference_month,
        "release_at_utc": _iso(evidence.release_at_utc),
        "raw_body_sha256": evidence.raw_body_sha256,
        "byte_count": evidence.byte_count,
        "raw_body_b64": base64.b64encode(evidence.raw_body).decode("ascii"),
        "manual_policy_identity": evidence.manual_policy_identity,
        "p1_authority_identity": evidence.p1_authority_identity,
        "p1_policy_identity": evidence.p1_policy_identity,
        "schema_version": evidence.schema_version,
        "evidence_id": evidence.evidence_id,
        "content_hash": evidence.content_hash,
        "research_only": evidence.research_only,
        "production_influence": str(evidence.production_influence),
        "scientific_evidence_authorized": False,
        "p12_frozen": False,
    }


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to overwrite existing receipt: {path}") from exc
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def main() -> int:
    args = _parser().parse_args()
    if not args.attest:
        raise SystemExit(
            "manual browser attestation required: rerun with --attest only if the "
            "file was saved from the exact official BLS CPI schedule page"
        )

    evidence = attest_and_import_manual_bls_cpi_calendar(
        args.file,
        reference_month=args.reference_month,
        operator_attestation=OPERATOR_ATTESTATION,
    )
    receipt = _receipt(evidence)
    payload = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _write_exclusive(args.output, payload)

    summary = {
        key: receipt[key]
        for key in (
            "record_type",
            "source_locator",
            "imported_at_utc",
            "reference_month",
            "release_at_utc",
            "raw_body_sha256",
            "byte_count",
            "evidence_id",
            "research_only",
            "production_influence",
            "scientific_evidence_authorized",
            "p12_frozen",
        )
    }
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
