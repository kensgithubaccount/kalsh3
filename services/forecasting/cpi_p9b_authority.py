"""Reviewed, immutable P9B.4 authority inputs.

These values are intentionally in runtime code.  A mutable manifest may describe
them, but cannot redefine them.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

CANONICAL_BASE: Final = "e8c6faff5a72db6010fd4ae22713b0a0831b947e"
CANONICAL_TREE: Final = "353aeba5d99c67c5baa4c72901965b323367ecbf"
P9A_MANIFEST_SHA256: Final = "6f162314cbeee641a3420a1e56597ef5dee5d9ef4af95f035c96d58c9bf7b01c"
P9A_FINAL_MANIFEST_SHA256: Final = (
    "565b1622b26ec3733b56862b3dd720b06d6b472154fef94db6239396306b345d"
)
P9A_ACQUISITION_SHA256: Final = "1ce8c675191aa15ae8f89c4f6a560ceaabdfd5c5bc4eb84289f45c092bf9de56"
P9A_APPROVED_ACQUISITION_DIGEST: Final = (
    "d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699"
)
P9A_MARKET_COUNT: Final = 474
P9A_EVENT_COUNT: Final = 60
P8_AUTHORITY_ARTIFACT_SHA256: Final = (
    "f189e95766307bccd7f44e01aa1a7d89950ce3050b657d34b0f9a844bdea6d7d"
)

APPROVED_ARTIFACTS: Final = (
    {
        "path": "raw/cftc-49335-cover.pdf",
        "sha256": "c13ece879021b9df29c3bac195b2c580f4876e9e77d7cde8fbd1a9c5b95da200",
        "bytes": 93269,
        "role": "taker",
        "identity": "CFTC-49335-COVER",
        "url": "https://www.cftc.gov/filings/orgrules/rule091222kexdcm001.pdf",
        "status": "exact",
    },
    {
        "path": "raw/cftc-49335-redline.pdf",
        "sha256": "2823becfd0252406cad15e51a1a4c68ca991a068fc36d2ade4c935116f83a85a",
        "bytes": 806026,
        "role": "taker",
        "identity": "CFTC-49335-REDLINE",
        "url": "https://www.cftc.gov/filings/orgrules/rule091222kexdcm002.pdf",
        "status": "exact",
    },
    {
        "path": "raw/cftc-49335-final-schedule.pdf",
        "sha256": "b002a5ae260c68f543a9c4630233c9d311c580ad70620e7db37eac14437007b3",
        "bytes": 106530,
        "role": "taker",
        "identity": "CFTC-49335-FINAL",
        "url": "https://www.cftc.gov/filings/orgrules/rule091222kexdcm003.pdf",
        "status": "exact",
    },
    {
        "path": "raw/cftc-rulebook-rule-3-6.pdf",
        "sha256": "d8d185862a439a8f1a178d5044bbe9c4ccfd931ae4a54619b9f2602493865c8f",
        "bytes": 865102,
        "role": "continuity",
        "identity": "CFTC-RULEBOOK-3.6-E",
        "url": "https://www.cftc.gov/filings/orgrules/rules07012525155.pdf",
        "status": "exact",
    },
    {
        "path": "raw/md-1-25-cv-01283-28-1.pdf",
        "sha256": "1185c41174304316cd6c68c6bd9eba3170e8082ff31a60b730b89b7da60b58e8",
        "bytes": 641165,
        "role": "taker",
        "identity": "MD-1:25-CV-01283-28-1",
        "url": "https://www.ingame.com/wp-content/uploads/2025/05/document.pdf",
        "status": "exact",
    },
    {
        "path": "raw/cftc-49335-index.html",
        "sha256": "cc813fd4676abbdf781d4fdf7d8429e37f1d7b319fea548a512240721c14eaed",
        "bytes": 31125,
        "role": "regulatory_index",
        "identity": "CFTC-49335-INDEX",
        "url": "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules/49335",
        "status": "exact",
    },
)

AUTHORITY_METADATA: Final = (
    {
        "identity": "CFTC-49335-COVER",
        "filing": "49335",
        "receipt_date": "2022-09-09",
        "effective_date": "2022-09-22",
        "scope": "taker",
        "kxcpi_applicability": "general",
        "status": "exact",
        "supersession": "amends prior general schedule",
    },
    {
        "identity": "CFTC-49335-REDLINE",
        "filing": "49335",
        "receipt_date": "2022-09-09",
        "effective_date": "2022-09-22",
        "scope": "taker",
        "kxcpi_applicability": "general",
        "status": "exact",
        "supersession": "amends prior general schedule",
    },
    {
        "identity": "CFTC-49335-FINAL",
        "filing": "49335",
        "receipt_date": "2022-09-09",
        "effective_date": "2022-09-22",
        "scope": "taker",
        "formula": "round_up(0.07 * C * P * (1-P))",
        "rounding": "next_cent",
        "kxcpi_applicability": "general",
        "status": "exact",
        "supersession": "superseded/continued by later snapshot",
    },
    {
        "identity": "CFTC-RULEBOOK-3.6-E",
        "filing": "rules07012525155",
        "rule": "3.6(e)",
        "scope": "continuity",
        "kxcpi_applicability": "general",
        "status": "exact",
        "supersession": "continuity constraint",
    },
    {
        "identity": "MD-1:25-CV-01283-28-1",
        "case": "KalshiEX LLC v. Martin et al.",
        "docket": "1:25-cv-01283-ABA",
        "document": "28-1",
        "filed_date": "2025-05-12",
        "last_updated": "2025-05-06",
        "scope": "taker",
        "formula": "round_up(0.07 * C * P * (1-P))",
        "rounding": "next_cent",
        "kxcpi_applicability": "general",
        "status": "exact",
        "supersession": "snapshot confirmation",
    },
    {
        "identity": "CFTC-49335-INDEX",
        "filing": "49335",
        "scope": "regulatory_index",
        "status": "exact",
        "supersession": "continuity inventory locator",
    },
)

P8_REFERENCE_EVENTS: Final = frozenset(
    {
        "CPI-22OCT",
        "CPI-22NOV",
        "CPI-22DEC",
        "CPI-23JAN",
        "CPI-23FEB",
        "CPI-23MAR",
        "CPI-23APR",
        "CPI-23MAY",
        "CPI-23JUN",
        "CPI-23JUL",
        "CPI-23AUG",
        "CPI-23SEP",
        "CPI-23OCT",
        "CPI-23NOV",
        "CPI-23DEC",
        "CPI-24JAN",
        "CPI-24FEB",
        "CPI-24MAR",
        "CPI-24APR",
        "CPI-24MAY",
        "CPI-24JUN",
        "CPI-24JUL",
        "CPI-24AUG",
        "CPI-24SEP",
        "CPI-24OCT",
        "KXCPI-24NOV",
        "KXCPI-24DEC",
        "KXCPI-25JAN",
    }
)


def approved_receipt_digest() -> str:
    payload = {
        "artifacts": APPROVED_ARTIFACTS,
        "authority_metadata": AUTHORITY_METADATA,
        "base": CANONICAL_BASE,
        "tree": CANONICAL_TREE,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


APPROVED_RECEIPT_SHA256: Final = "6e5b60297615baf8b3e2f1fff25fb8b1b6b673fed0fa29c12de53ecf51314568"
