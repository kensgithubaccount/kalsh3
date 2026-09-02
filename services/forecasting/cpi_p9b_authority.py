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
FILING_INVENTORY_SHA256: Final = "3ae3383b7cf5dd089a76357a8557cc2d67adb597284c0b141780ca58134af16f"
FILING_INVENTORY_BYTES: Final = 9533

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
        "identity": "CFTC-RULEBOOK-3.10-E",
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
    {
        "path": "raw/cftc-kex-fee-filing-inventory.json",
        "sha256": "3ae3383b7cf5dd089a76357a8557cc2d67adb597284c0b141780ca58134af16f",
        "bytes": 9533,
        "role": "continuity_inventory",
        "identity": "CFTC-KEX-FEE-INVENTORY-2022-09-22-2025-05-06",
        "url": "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules?Organization=KEX&Receipt_Date_From=2022-09-22&Receipt_Date_To=2025-05-06&Show_All=1",
        "status": "continuity_supported",
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
        "identity": "CFTC-RULEBOOK-3.10-E",
        "filing": "rules07012525155",
        "rule": "3.10(e)",
        "effective_interval": "2025-07-16 onward only; historical interval not proven",
        "relevant_text": (
            "The Exchange may establish dues, fees, and expenses payable by Members "
            "and must file applicable rule changes before implementation under the "
            "governing filing regime."
        ),
        "scope": "continuity",
        "kxcpi_applicability": "general",
        "status": "continuity_supported",
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
    {
        "identity": "CFTC-KEX-FEE-INVENTORY-2022-09-22-2025-05-06",
        "effective_interval": "2022-09-22 through 2025-05-06 search window",
        "scope": "continuity_inventory",
        "kxcpi_applicability": "general inventory; individual programs conditional or excluded",
        "status": "continuity_supported",
        "supersession": "replaces single-filing non-exhaustive locator",
    },
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


APPROVED_RECEIPT_SHA256: Final = "8328bf6a417fdc042e1e0852f34896c205b306facb443b0dde0cbe6a657951b5"
