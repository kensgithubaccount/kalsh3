"""Fresh-process acceptance for the honest, currently incomplete GDP run.

The fresh-process subprocess replaces ONLY network transport (a fake
``http.client.HTTPSConnection``, installed before any project module that
captures it at import time) with exact recorded raw responses. It never
monkeypatches parsers, authority verdicts, classifiers, schedule authority,
fee authority, decision classification, or persistence logic: the real
acquisition/parser/authority chain executes for real against canned bytes, and
a distinct guard blocks any genuine socket connection so the test provably
does not depend on external network access.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from services.forward_reality.trial_ledger import TrialLedger
from services.production_gdp_strategy.gdp_persistence import (
    RESEARCH_STORAGE_ROOT_ENV,
    DecisionArchive,
    GDPPersistenceError,
)
from services.production_gdp_strategy.one_decision import DecisionClass

FIXTURES = Path(__file__).parent / "fixtures"


def _kalshi_event_bytes() -> bytes:
    # Byte-identical to the accepted D1-G3-S1 schedule-authority fixture: a
    # genuinely reviewed KXGDP-26OCT30 event with one eligible market.
    return (FIXTURES / "d1_g3_kxgdp_q3_2026_event.json").read_bytes()


def _bea_schedule_bytes() -> bytes:
    return (FIXTURES / "d1_g3_bea_schedule_q3_2026.html").read_bytes()


def _gdpnow_commentary_bytes() -> bytes:
    return (
        b"<h2>September 3, 2026</h2><p>The GDPNow model estimate for real GDP growth "
        b"(seasonally adjusted annual rate) in the third quarter of 2026 is "
        b"<strong>4.7 percent</strong> on September 3, down from 4.8 percent.</p>"
    )


def _fee_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.7\nKalshi Fee Schedule\nLast updated and effective: July 7, 2026\n"
        b"The current general fee is determined by the following formula.\n"
        b"fees = round up (M * 0.07 * C * P * (1-P))\nMaker Fees\n"
        b"fees = round up (M * 0.0175 * C * P * (1-P))\n"
        b"P is contract price in dollars and C is contract quantity\n"
        b"Maker multiplier Taker multiplier KXGDP 1 1\n%%EOF"
    )


def _fee_changes_bytes() -> bytes:
    return b'{"series_fee_change_arr":[]}'


# The CHILD process installs this fake HTTPSConnection BEFORE importing any
# project module. schedule_authority._make_evidence_issuer() captures
# http.client.HTTPSConnection as a parameter default evaluated once at
# schedule_authority's own import time, so the patch must land first. Once
# patched, it also transparently covers fee_authority's urllib.request calls
# (urllib's HTTPSHandler looks up http.client.HTTPSConnection at call time)
# and gdpnow_source_acquisition / market_universe.public_read's direct
# http.client usage (also looked up at call time).
_CHILD = r"""
import base64, hashlib, json, socket, sys
from email import message_from_string

_RECORDED = {
    ("www.atlantafed.org", "/research-and-data/data/gdpnow/current-and-past-gdpnow-commentaries"): (
        200, base64.b64decode(%(gdpnow_b64)r), {"content-type": "text/html"}
    ),
    ("external-api.kalshi.com", "/trade-api/v2/events/KXGDP-26OCT30?with_nested_markets=true"): (
        200, base64.b64decode(%(event_b64)r),
        {
            "content-type": "application/json",
            "x-kalshi-event-market-count": "1",
            "x-kalshi-event-pagination-terminal": "true",
        },
    ),
    ("www.bea.gov", "/news/schedule/"): (
        200, base64.b64decode(%(bea_b64)r), {"content-type": "text/html"}
    ),
    ("kalshi.com", "/docs/kalshi-fee-schedule.pdf"): (
        200, base64.b64decode(%(pdf_b64)r), {"content-type": "application/pdf"}
    ),
    (
        "external-api.kalshi.com",
        "/trade-api/v2/series/fee_changes?series_ticker=KXGDP&show_historical=true",
    ): (
        200, base64.b64decode(%(fee_changes_b64)r), {"content-type": "application/json"}
    ),
}
_SEEN = []


def _blocked_connect(self, *a, **k):
    raise OSError("real network access is blocked in this hermetic fresh-process test")


socket.socket.connect = _blocked_connect
socket.socket.connect_ex = _blocked_connect


class _FakeHTTPResponse:
    def __init__(self, status, body, headers):
        self.status = status
        self.reason = "OK"
        self._body = body
        header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
        self.headers = message_from_string(header_lines)
        self.msg = self.headers

    def read(self, amt=None):
        if amt is None or amt < 0:
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:amt], self._body[amt:]
        return data

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def getheaders(self):
        return list(self.headers.items())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _RecordedHTTPSConnection:
    debuglevel = 0
    _http_vsn = 11
    _http_vsn_str = "HTTP/1.1"

    def __init__(self, host, *, timeout=None, context=None, **kwargs):
        self.host = host
        self._path = None

    def set_debuglevel(self, level):
        pass

    def request(self, method, path, body=None, headers=None, **kwargs):
        self._path = path
        _SEEN.append([self.host, method, path])

    def getresponse(self):
        key = (self.host, self._path)
        entry = _RECORDED.get(key)
        if entry is None:
            raise OSError(f"no recorded transport response for {self.host}{self._path}")
        status, body, headers = entry
        return _FakeHTTPResponse(status, body, headers)

    def close(self):
        pass


import http.client

http.client.HTTPSConnection = _RecordedHTTPSConnection

# ONLY network transport was replaced above. Everything below is the real,
# unmodified acquisition/parser/authority/decision/persistence chain.
from services.production_gdp_strategy.gdp_persistence import (
    open_default_research_storage,
    replay_gdp_decision,
)
from services.production_gdp_strategy.one_decision import run_one_research_decision

seen_log_path = sys.argv[1]
mode = sys.argv[2]
try:
    if mode == "run":
        result = run_one_research_decision()
        print(json.dumps({
            "trial_id": result.trial_id,
            "decision_id": result.decision_id,
            "payload_hash": result.payload_hash,
            "classification": result.classification.value,
            "research_only": result.research_only,
            "production_influence": str(result.production_influence),
        }))
    else:
        trial_id = sys.argv[3]
        ledger, archive = open_default_research_storage()
        result = replay_gdp_decision(ledger, archive, trial_id)
        print(json.dumps({
            "trial_id": result.trial_id,
            "decision_id": result.decision_id,
            "payload_hash": result.payload_hash,
            "classification": result.classification.value,
            "research_only": result.research_only,
            "production_influence": str(result.production_influence),
        }))
finally:
    with open(seen_log_path, "w") as fh:
        json.dump(_SEEN, fh)
"""


def _render_child() -> str:
    import base64

    return _CHILD % {
        "gdpnow_b64": base64.b64encode(_gdpnow_commentary_bytes()).decode(),
        "event_b64": base64.b64encode(_kalshi_event_bytes()).decode(),
        "bea_b64": base64.b64encode(_bea_schedule_bytes()).decode(),
        "pdf_b64": base64.b64encode(_fee_pdf_bytes()).decode(),
        "fee_changes_b64": base64.b64encode(_fee_changes_bytes()).decode(),
    }


def _run_child(
    storage_root: Path, seen_log: Path, mode: str, trial_id: str = ""
) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ)
    env[RESEARCH_STORAGE_ROOT_ENV] = str(storage_root)
    args = [sys.executable, "-c", _render_child(), str(seen_log), mode]
    if trial_id:
        args.append(trial_id)
    return subprocess.run(args, capture_output=True, text=True, env=env)


def _seen_hosts(seen_log: Path) -> list[str]:
    return [entry[0] for entry in json.loads(seen_log.read_text())]


_EXPECTED_ACQUISITION_HOSTS = {
    "www.atlantafed.org",
    "external-api.kalshi.com",
    "www.bea.gov",
    "kalshi.com",
}


def test_gdp_public_composition_persists_and_replays_in_fresh_process(tmp_path: Path) -> None:
    storage_root = tmp_path / "gdp-research"
    first_seen = tmp_path / "first.seen.json"
    completed = _run_child(storage_root, first_seen, "run")
    assert completed.returncode == 0, completed.stderr
    first = json.loads(completed.stdout)
    assert first["classification"] == DecisionClass.EVIDENCE_INCOMPLETE.value
    assert first["research_only"] is True
    assert first["production_influence"] == "0"
    # Proves the real chain reached genuine, honestly-incomplete fee
    # authority rather than an earlier swallowed acquisition failure: every
    # network-bearing component the public composition actually reaches was
    # hit through the recorded transport, with no live internet involved.
    assert set(_seen_hosts(first_seen)) >= _EXPECTED_ACQUISITION_HOSTS

    replay_seen = tmp_path / "replay.seen.json"
    replayed_proc = _run_child(storage_root, replay_seen, "replay", str(first["trial_id"]))
    assert replayed_proc.returncode == 0, replayed_proc.stderr
    replayed = json.loads(replayed_proc.stdout)
    expected = {
        key: first[key]
        for key in (
            "trial_id",
            "decision_id",
            "payload_hash",
            "classification",
            "research_only",
            "production_influence",
        )
    }
    assert replayed == expected
    # Replay reconstructs from the authenticated archive; it must not
    # re-acquire anything over the (recorded) network.
    assert _seen_hosts(replay_seen) == []

    duplicate_seen = tmp_path / "duplicate.seen.json"
    duplicate = _run_child(storage_root, duplicate_seen, "run")
    assert duplicate.returncode != 0
    assert "duplicate prospective GDP attempt" in duplicate.stderr
    # The duplicate attempt must be rejected BEFORE any second acquisition.
    assert _seen_hosts(duplicate_seen) == []


@pytest.mark.parametrize(
    "mutation",
    ["payload", "classification", "research", "influence", "trial", "event", "mac"],
)
def test_gdp_decision_archive_mutations_fail_closed(tmp_path: Path, mutation: str) -> None:
    storage_root = tmp_path / "gdp-research"
    seen_log = tmp_path / "run.seen.json"
    completed = _run_child(storage_root, seen_log, "run")
    assert completed.returncode == 0, completed.stderr
    first = json.loads(completed.stdout)
    journal = storage_root / "decisions" / "decisions.journal"
    record = json.loads(journal.read_text())
    if mutation == "mac":
        record["issuer_mac"] = "0" * 64
    elif mutation == "payload":
        record["payload_hash"] = "0" * 64
    elif mutation == "classification":
        record["payload"]["classification"]["value"] = "TRADE_YES"
    elif mutation == "research":
        record["payload"]["research_only"]["value"] = False
    elif mutation == "influence":
        record["payload"]["production_influence"]["value"] = "1"
    elif mutation == "trial":
        record["trial_id"] = "wrong-trial"
    else:
        record["underlying_event_id"] = "wrong-event"
    journal.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(GDPPersistenceError):
        DecisionArchive(storage_root / "decisions").load(str(first["trial_id"]))


def test_gdp_archive_truncation_and_rebuilt_sqlite_index_fail_closed(tmp_path: Path) -> None:
    storage_root = tmp_path / "gdp-research"
    seen_log = tmp_path / "run.seen.json"
    completed = _run_child(storage_root, seen_log, "run")
    assert completed.returncode == 0, completed.stderr
    first = json.loads(completed.stdout)
    journal = storage_root / "decisions" / "decisions.journal"
    journal.write_bytes(journal.read_bytes()[:-1])
    with pytest.raises(GDPPersistenceError):
        DecisionArchive(storage_root / "decisions").load(str(first["trial_id"]))

    # The ledger's SQLite index is a rebuildable cache, never the authority.
    ledger_path = storage_root / "ledger.sqlite"
    with sqlite3.connect(ledger_path) as db:
        db.execute("DROP TABLE trial_index")
    restored = TrialLedger(ledger_path)
    assert restored.get(str(first["trial_id"])).trial_id == first["trial_id"]


def test_incomplete_public_decision_has_no_economic_outcome(tmp_path: Path) -> None:
    storage_root = tmp_path / "gdp-research"
    seen_log = tmp_path / "run.seen.json"
    completed = _run_child(storage_root, seen_log, "run")
    assert completed.returncode == 0, completed.stderr
    first = json.loads(completed.stdout)
    assert first["classification"] == DecisionClass.EVIDENCE_INCOMPLETE.value
    assert not (storage_root / "decisions" / f"{first['decision_id']}.settlement.json").exists()


def test_archive_key_replacement_invalidates_all_prior_records(tmp_path: Path) -> None:
    """A corrupted/replaced signing key must fail closed, not silently re-authenticate."""
    storage_root = tmp_path / "gdp-research"
    seen_log = tmp_path / "run.seen.json"
    completed = _run_child(storage_root, seen_log, "run")
    assert completed.returncode == 0, completed.stderr
    first = json.loads(completed.stdout)
    key_path = storage_root / "decisions" / "decisions.issuer-key"
    key_path.write_bytes(b"\x01" * 32)
    with pytest.raises(GDPPersistenceError):
        DecisionArchive(storage_root / "decisions").load(str(first["trial_id"]))
