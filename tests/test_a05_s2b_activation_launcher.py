import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_a05_s2a_prospective_shadow as launcher
from services.prospective_shadow.kernel import ShadowKernelError, build_start_receipt

SHA = "a" * 40
TREE = "b" * 40


def _receipt() -> dict[str, object]:
    return build_start_receipt(
        canonical_base_sha="c" * 40,
        canonical_base_tree="d" * 40,
        start_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
        source_identities={"kalshi_public_read": "public-read-v1"},
        fee_policy_id="research-only-fees-v1",
    )


def _args(tmp_path: Path, *mode: str, activation: bool = True) -> list[str]:
    archive = tmp_path / "archive.sqlite"
    archive.touch()
    receipt = tmp_path / "receipt.json"
    receipt_value = _receipt()
    receipt.write_text(json.dumps(receipt_value), encoding="utf-8")
    store = tmp_path / "store.sqlite"
    with sqlite3.connect(store) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS start_receipt (id INTEGER PRIMARY KEY, canonical_json TEXT)"
        )
        db.execute("CREATE TABLE IF NOT EXISTS observations (sequence INTEGER PRIMARY KEY)")
        db.execute(
            "INSERT OR REPLACE INTO start_receipt VALUES (1, ?)",
            (launcher.prospective_kernel.canonical_json(receipt_value),),
        )
    values = [
        "--runtime-git-sha",
        SHA,
        "--runtime-git-tree",
        TREE,
        "--archive",
        str(archive),
        "--store",
        str(store),
        "--start-receipt",
        str(receipt),
        *mode,
    ]
    return (["--s2a-enabled"] if activation else []) + values


@pytest.fixture
def identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "_checkout_identity", lambda _root: (SHA, TREE))
    monkeypatch.setattr(launcher, "_verify_module_binding", lambda _root: None)


def test_activation_and_modes_are_explicit(tmp_path: Path, identity: None) -> None:
    with pytest.raises(RuntimeError, match="--s2a-enabled"):
        launcher.main(_args(tmp_path, "--once", activation=False))
    with pytest.raises(SystemExit):
        launcher.main(_args(tmp_path, activation=True))
    with pytest.raises(SystemExit):
        launcher.main(_args(tmp_path, "--once", "--forever"))


def test_runtime_sha_and_tree_are_required(tmp_path: Path) -> None:
    args = _args(tmp_path, "--once")
    for option in ("--runtime-git-sha", "--runtime-git-tree"):
        missing = args[: args.index(option)] + args[args.index(option) + 2 :]
        with pytest.raises(SystemExit):
            launcher.main(missing)


@pytest.mark.parametrize(
    ("sha", "tree", "message"),
    [("a", TREE, "SHA"), (SHA, "b", "tree"), ("e" * 40, TREE, "SHA"), (SHA, "f" * 40, "tree")],
)
def test_identity_must_match_verified_checkout(
    tmp_path: Path, identity: None, sha: str, tree: str, message: str
) -> None:
    args = _args(tmp_path, "--once")
    args[args.index("--runtime-git-sha") + 1] = sha
    args[args.index("--runtime-git-tree") + 1] = tree
    with pytest.raises(RuntimeError, match=message):
        launcher.main(args)


def test_matching_and_outside_module_paths(tmp_path: Path) -> None:
    assert launcher._module_path_is_in_root(launcher.prospective_runner, launcher.RUNTIME_ROOT)
    assert launcher._module_path_is_in_root(launcher.prospective_kernel, launcher.RUNTIME_ROOT)
    outside = SimpleNamespace(__file__=str(tmp_path / "other" / "runner.py"))
    assert not launcher._module_path_is_in_root(outside, launcher.RUNTIME_ROOT)


def test_external_module_binding_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = launcher.prospective_runner.__file__
    monkeypatch.setattr(launcher.prospective_runner, "__file__", str(tmp_path / "runner.py"))
    with pytest.raises(RuntimeError, match="outside"):
        launcher._verify_module_binding(launcher.RUNTIME_ROOT)
    monkeypatch.setattr(launcher.prospective_runner, "__file__", original)


def test_receipt_is_validated_and_not_rewritten(tmp_path: Path, identity: None) -> None:
    args = _args(tmp_path, "--once")
    receipt_path = Path(args[args.index("--start-receipt") + 1])
    before = receipt_path.read_bytes()
    args[args.index("--archive") + 1] = str(tmp_path / "missing")
    with pytest.raises(RuntimeError, match="archive"):
        launcher.main(args)
    assert receipt_path.read_bytes() == before
    receipt_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ShadowKernelError):
        launcher.main(args)


def test_store_paths_and_identity_fail_closed(tmp_path: Path, identity: None) -> None:
    args = _args(tmp_path, "--once")
    store_index = args.index("--store") + 1
    archive_index = args.index("--archive") + 1
    receipt_index = args.index("--start-receipt") + 1
    original_store = args[store_index]

    args[store_index] = str(tmp_path / "missing.sqlite")
    with pytest.raises(RuntimeError, match="does not exist"):
        launcher.main(args)
    args[store_index] = str(tmp_path / "store-dir")
    Path(args[store_index]).mkdir()
    with pytest.raises(RuntimeError, match="does not exist"):
        launcher.main(args)

    args[store_index] = args[archive_index]
    with pytest.raises(RuntimeError, match="distinct"):
        launcher.main(args)
    args[store_index] = args[receipt_index]
    with pytest.raises(RuntimeError, match="distinct"):
        launcher.main(args)

    args[store_index] = original_store
    with sqlite3.connect(original_store) as db:
        db.execute("UPDATE start_receipt SET canonical_json='{}' WHERE id=1")
    with pytest.raises(RuntimeError, match="does not match"):
        launcher.main(args)


def test_store_preflight_rejects_non_a01_database(tmp_path: Path, identity: None) -> None:
    args = _args(tmp_path, "--once")
    store = Path(args[args.index("--store") + 1])
    with sqlite3.connect(store) as db:
        db.execute("DROP TABLE observations")
    with pytest.raises(RuntimeError, match=r"not an A0\.1"):
        launcher.main(args)


def test_once_injects_runtime_and_returns_incomplete_status(
    tmp_path: Path, identity: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class NoopStore:
        def __init__(self, path: Path, receipt: dict[str, object]) -> None:
            captured["store"] = (path, receipt)

    def run_once(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            cycle_id="cycle-test", complete=False, observations=(), diagnostics={}
        )

    monkeypatch.setattr(launcher, "ShadowObservationStore", NoopStore)
    monkeypatch.setattr(launcher.prospective_runner, "run_once", run_once)
    assert launcher.main(_args(tmp_path, "--once")) == 2
    assert captured["s2a_enabled"] is True
    assert captured["runtime"] == launcher.RuntimeIdentity(SHA, TREE)


def test_once_complete_and_forever_use_canonical_runner(
    tmp_path: Path, identity: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(launcher, "ShadowObservationStore", lambda *_: object())
    monkeypatch.setattr(
        launcher.prospective_runner,
        "run_once",
        lambda **kwargs: (
            calls.append(("once", kwargs))
            or SimpleNamespace(
                cycle_id="cycle-test", complete=True, observations=(), diagnostics={}
            )
        ),
    )
    assert launcher.main(_args(tmp_path, "--once")) == 0
    assert calls[0][1]["s2a_enabled"] is True

    def run_forever(**kwargs: object) -> None:
        calls.append(("forever", kwargs))

    monkeypatch.setattr(launcher.prospective_runner, "run_forever", run_forever)
    assert launcher.main(_args(tmp_path, "--forever")) == 0
    assert calls[-1][0] == "forever"
    assert calls[-1][1]["interval_seconds"] == 900.0
    assert calls[-1][1]["s2a_enabled"] is True


def test_launcher_has_no_legacy_runner_invocation() -> None:
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "s2a_enabled=False" not in source
    assert '"s2a_enabled": True' in source
