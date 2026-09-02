"""Reviewed, immutable P9B.4 authority inputs.

These values are intentionally in runtime code.  A mutable manifest may describe
them, but cannot redefine them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final

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
FILING_INVENTORY_SHA256: Final = "a979dc5e4a19c187e7d40efc384cc9737c33cd03d9478788326bd3d0f4b689a2"
FILING_INVENTORY_BYTES: Final = 9671
OFFICIAL_INVENTORY_RESPONSE_SHA256: Final = (
    "44062965f754657a4d06a7c20371205b580edc7b2baa9b3d380ba90b3f27ee8f"
)
OFFICIAL_INVENTORY_RESPONSE_BYTES: Final = 52768
CFR_2022_SHA256: Final = "f6b63052591c0735b650e6fd96a211b8d2e4823bab6d3d366a5067e5a6622757"
CFR_2022_BYTES: Final = 231501
CFR_2025_SHA256: Final = "f10d901fcc847b2635782fe62f212724f876fcbd7df314c221505b957b8484ed"
CFR_2025_BYTES: Final = 250079


class FeeAuthorityUnavailable(ValueError):
    """Raised when a fee formula is requested from non-exact evidence."""


@dataclass(frozen=True)
class ConsumableTakerFee:
    authority_identity: str
    formula: str
    rounding: str


def consume_taker_fee_authority(row: dict[str, Any]) -> ConsumableTakerFee:
    """Return a fee regime only for an explicitly exact authority row."""
    if row.get("status") != "exact" or not row.get("exact_fee_authority"):
        raise FeeAuthorityUnavailable(
            f"fee authority is not consumable for status={row.get('status')!r}"
        )
    formula = row.get("formula")
    rounding = row.get("rounding")
    if not isinstance(formula, str) or not isinstance(rounding, str):
        raise FeeAuthorityUnavailable("exact fee row lacks formula or rounding")
    return ConsumableTakerFee(row["authority_identity"], formula, rounding)


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
        "path": "raw/cftc-rulebook-july-2025.pdf",
        "sha256": "d8d185862a439a8f1a178d5044bbe9c4ccfd931ae4a54619b9f2602493865c8f",
        "bytes": 865102,
        "role": "rulebook_snapshot",
        "identity": "CFTC-RULEBOOK-JULY-2025-RULE-3.10",
        "url": "https://www.cftc.gov/filings/orgrules/rules07012525155.pdf",
        "status": "snapshot",
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
        "sha256": "a979dc5e4a19c187e7d40efc384cc9737c33cd03d9478788326bd3d0f4b689a2",
        "bytes": 9671,
        "role": "continuity_inventory",
        "identity": "CFTC-KEX-FEE-INVENTORY-2022-09-22-2025-05-06",
        "url": "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules?Organization=KEX&Receipt_Date_From=2022-09-22&Receipt_Date_To=2025-05-06&Show_All=1",
        "status": "research_locator",
    },
    {
        "path": "raw/cftc-kex-fee-index-response.html",
        "sha256": "44062965f754657a4d06a7c20371205b580edc7b2baa9b3d380ba90b3f27ee8f",
        "bytes": 52768,
        "role": "official_inventory_response",
        "identity": "CFTC-KEX-FEE-INDEX-RESPONSE-2022-09-22-2025-05-06",
        "url": "https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizationRules?Organization=KEX&Receipt_Date_From=2022-09-22&Receipt_Date_To=2025-05-06&Show_All=1",
        "status": "locator",
    },
    {
        "path": "raw/cfr-2022-title17-part40.pdf",
        "sha256": "f6b63052591c0735b650e6fd96a211b8d2e4823bab6d3d366a5067e5a6622757",
        "bytes": 231501,
        "role": "historical_regulatory_authority",
        "identity": "CFR-2022-T17-P40",
        "url": "https://www.govinfo.gov/content/pkg/CFR-2022-title17-vol1/pdf/CFR-2022-title17-vol1-part40.pdf",
        "status": "snapshot",
    },
    {
        "path": "raw/cfr-2025-title17-part40.pdf",
        "sha256": "f10d901fcc847b2635782fe62f212724f876fcbd7df314c221505b957b8484ed",
        "bytes": 250079,
        "role": "historical_regulatory_authority",
        "identity": "CFR-2025-T17-P40",
        "url": "https://www.govinfo.gov/content/pkg/CFR-2025-title17-vol1/pdf/CFR-2025-title17-vol1-part40.pdf",
        "status": "snapshot",
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
        "identity": "CFTC-RULEBOOK-JULY-2025-RULE-3.10",
        "filing": "rules07012525155",
        "rule": "3.10",
        "effective_interval": (
            "2025-07-16 onward only; Rule 3.10 contains subsections (a)-(d), "
            "with (b) addressing trading fees published on the website"
        ),
        "scope": "snapshot",
        "kxcpi_applicability": "general",
        "status": "snapshot",
        "supersession": "snapshot only",
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
        "status": "research_locator",
        "supersession": "replaces single-filing non-exhaustive locator",
    },
    {
        "identity": "CFR-2022-T17-P40",
        "effective_interval": "2022-07-01 through 2023-06-30 annual CFR snapshot",
        "operative_subsections": ["40.5", "40.6"],
        "scope": "registered_entity_rule_changes",
        "fee_exceptions": (
            "Fee provisions include exceptions and thresholds; applicability to "
            "every KXCPI website revision is not established here."
        ),
        "kxcpi_applicability": (
            "potentially general DCM framework; historical application unresolved"
        ),
        "status": "snapshot",
        "supersession": "superseded by later annual CFR snapshot",
    },
    {
        "identity": "CFR-2025-T17-P40",
        "effective_interval": "2025-04-01 through 2026-03-31 annual CFR snapshot",
        "operative_subsections": ["40.5", "40.6"],
        "scope": "registered_entity_rule_changes",
        "fee_exceptions": (
            "Fee provisions include exceptions and thresholds; applicability to "
            "every KXCPI website revision is not established here."
        ),
        "kxcpi_applicability": (
            "potentially general DCM framework; historical application unresolved"
        ),
        "status": "snapshot",
        "supersession": "current comparison snapshot",
    },
)

APPROVED_TIMELINES: Final = {
    "endpoint_observations": [
        {
            "observed_date": "2022-09-22",
            "status": "exact",
            "authority_type": "taker",
            "authority_identity": "CFTC-49335-FINAL",
            "formula": "round_up(0.07 * C * P * (1-P))",
            "rounding": "next_cent",
            "exact_fee_authority": True,
            "economics_usable": True,
        },
        {
            "observed_date": "2025-05-06",
            "status": "exact",
            "authority_type": "taker",
            "authority_identity": "MD-1:25-CV-01283-28-1",
            "formula": "round_up(0.07 * C * P * (1-P))",
            "rounding": "next_cent",
            "exact_fee_authority": True,
            "economics_usable": True,
        },
    ],
    "taker": [
        {
            "start_date": None,
            "end_date": "2022-09-24",
            "status": "unknown",
            "authority_type": "taker",
            "authority_identity": "UNKNOWN_PRE_EFFECTIVE_DATE",
            "formula": None,
            "rounding": None,
            "exact_fee_authority": False,
            "economics_usable": False,
            "kxcpi_applicability": "unknown",
        },
        {
            "start_date": "2022-09-24",
            "end_date": "2025-05-06",
            "status": "interval_unproven_between_matching_endpoints",
            "authority_type": "taker",
            "authority_identity": "UNPROVEN_BETWEEN_CFTC_49335_AND_MD_28_1",
            "formula": None,
            "rounding": None,
            "exact_fee_authority": False,
            "economics_usable": False,
            "kxcpi_applicability": "unknown",
            "notes": "Matching endpoint formulas are informational only; no continuity claim.",
        },
        {
            "start_date": "2025-05-06",
            "end_date": None,
            "status": "locator_only",
            "authority_type": "taker",
            "authority_identity": "OCT_2025_LOCATOR_ONLY",
            "formula": None,
            "rounding": None,
            "exact_fee_authority": False,
            "economics_usable": False,
            "kxcpi_applicability": "unknown",
        },
    ],
    "maker": [
        {
            "start_date": None,
            "end_date": None,
            "status": "unknown",
            "authority_type": "maker",
            "authority_identity": "MAKER_UNRESOLVED",
            "formula": None,
            "rounding": None,
            "exact_fee_authority": False,
            "economics_usable": False,
            "kxcpi_applicability": "unknown",
        }
    ],
}


def approved_receipt_digest() -> str:
    payload = {
        "artifacts": APPROVED_ARTIFACTS,
        "authority_metadata": AUTHORITY_METADATA,
        "base": CANONICAL_BASE,
        "tree": CANONICAL_TREE,
        "timelines": APPROVED_TIMELINES,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


APPROVED_RECEIPT_SHA256: Final = "b8f6005d07bbd595a9e84bdc367415ce22d9404f8c472bcefe34f256ce089859"
