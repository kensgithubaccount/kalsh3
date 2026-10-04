from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D1 = ROOT / "experiments" / "wn_multicity_prospective_v1"
FINAL = ROOT / "experiments" / "wn_multicity_cloud_c1_final"
FROZEN_D1 = FINAL / "frozen" / "wn_multicity_prospective_v1"

PROTOCOL_SHA256 = "56fb3c00eec38286b64e57652dc822145a7fbcbe9d51e52693eb2f16943fd346"
AMENDMENT_SHA256 = "b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_multicity_d1_manifest_matches_imported_bytes() -> None:
    manifest = json.loads((D1 / "SHA256SUMS.json").read_text())["sha256"]
    assert len(manifest) == 20
    for relative, expected in manifest.items():
        path = D1 / relative
        assert path.is_file(), relative
        assert _sha(path) == expected, relative

    assert _sha(D1 / "protocol.json") == PROTOCOL_SHA256
    assert _sha(D1 / "pre_first_event_amendment_v1.json") == AMENDMENT_SHA256


def test_final_exact_digest_review_matches_imported_bytes() -> None:
    reviewed = json.loads((FINAL / "independent_review_exact_digest.json").read_text())[
        "reviewed_source_sha256"
    ]
    assert len(reviewed) == 32
    for relative, expected in reviewed.items():
        path = FINAL / relative
        assert path.is_file(), relative
        assert _sha(path) == expected, relative


def test_final_frozen_d1_copy_is_byte_identical() -> None:
    for source in sorted(path for path in D1.iterdir() if path.is_file()):
        frozen = FROZEN_D1 / source.name
        if frozen.exists():
            assert source.read_bytes() == frozen.read_bytes(), source.name
