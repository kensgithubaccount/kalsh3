"""Fresh-process acceptance for the honest, currently incomplete GDP run."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from services.forward_reality.trial_ledger import TrialLedger
from services.production_gdp_strategy.gdp_persistence import (
    DecisionArchive,
    GDPPersistenceError,
)
from services.production_gdp_strategy.one_decision import DecisionClass

_CHILD = """
import json, sys
from pathlib import Path
from services.forward_reality.trial_ledger import TrialLedger
from services.production_gdp_strategy.gdp_persistence import (
    DecisionArchive,
    replay_gdp_decision,
    run_one_persisted_research_decision,
)
ledger = TrialLedger(Path(sys.argv[1]))
archive = DecisionArchive(Path(sys.argv[2]))
if sys.argv[3] == 'run':
    result = run_one_persisted_research_decision(ledger, archive)
    print(json.dumps({
        'trial_id': result.trial_id, 'decision_id': result.decision_id,
        'payload_hash': result.payload_hash,
        'classification': result.classification.value,
        'research_only': result.research_only,
        'production_influence': str(result.production_influence),
    }))
else:
    result = replay_gdp_decision(ledger, archive, sys.argv[4])
    print(json.dumps({
        'trial_id': result.trial_id, 'decision_id': result.decision_id,
        'payload_hash': result.payload_hash,
        'classification': result.classification.value,
    }))
"""


def _process(ledger: Path, archive: Path, mode: str, trial_id: str = "") -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, str(ledger), str(archive), mode, trial_id],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_gdp_public_composition_persists_and_replays_in_fresh_process(tmp_path: Path) -> None:
    ledger_path = tmp_path / "gdp-ledger.sqlite"
    archive_path = tmp_path / "gdp-decisions"
    first = _process(ledger_path, archive_path, "run")
    assert first["classification"] == DecisionClass.EVIDENCE_INCOMPLETE.value
    assert first["research_only"] is True
    assert first["production_influence"] == "0"

    replayed = _process(ledger_path, archive_path, "replay", str(first["trial_id"]))
    expected = {
        key: first[key] for key in ("trial_id", "decision_id", "payload_hash", "classification")
    }
    assert replayed == expected

    duplicate = subprocess.run(
        [sys.executable, "-c", _CHILD, str(ledger_path), str(archive_path), "run", ""],
        capture_output=True,
        text=True,
    )
    assert duplicate.returncode != 0
    assert "duplicate prospective GDP attempt" in duplicate.stderr


@pytest.mark.parametrize(
    "mutation",
    ["payload", "classification", "research", "influence", "trial", "event", "mac"],
)
def test_gdp_decision_archive_mutations_fail_closed(tmp_path: Path, mutation: str) -> None:
    ledger_path = tmp_path / "ledger.sqlite"
    archive_path = tmp_path / "archive"
    first = _process(ledger_path, archive_path, "run")
    journal = archive_path / "decisions.journal"
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
        DecisionArchive(archive_path).load(str(first["trial_id"]))


def test_gdp_archive_truncation_and_rebuilt_sqlite_index_fail_closed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.sqlite"
    archive_path = tmp_path / "archive"
    first = _process(ledger_path, archive_path, "run")
    journal = archive_path / "decisions.journal"
    journal.write_bytes(journal.read_bytes()[:-1])
    with pytest.raises(GDPPersistenceError):
        DecisionArchive(archive_path).load(str(first["trial_id"]))

    # The ledger's SQLite index is a rebuildable cache, never the authority.
    with sqlite3.connect(ledger_path) as db:
        db.execute("DROP TABLE trial_index")
    restored = TrialLedger(ledger_path)
    assert restored.get(str(first["trial_id"])).trial_id == first["trial_id"]


def test_incomplete_public_decision_has_no_economic_outcome(tmp_path: Path) -> None:
    first = _process(tmp_path / "ledger.sqlite", tmp_path / "archive", "run")
    assert first["classification"] == DecisionClass.EVIDENCE_INCOMPLETE.value
    assert not (tmp_path / "archive" / f"{first['decision_id']}.settlement.json").exists()
