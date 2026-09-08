"""Run the reviewed A0.5 S2A prospective-shadow launcher.

This entry point is deliberately research-only: it wires the existing runner and
kernel, but contains no account, credential, order, or execution path.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Direct script execution places ``scripts/`` first on sys.path.  Put the checkout
# root first so the imported implementation is bound to this launcher checkout.
RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from services.prospective_shadow import kernel as prospective_kernel  # noqa: E402
from services.prospective_shadow import runner as prospective_runner  # noqa: E402
from services.prospective_shadow.kernel import (  # noqa: E402
    RuntimeIdentity,
    ShadowObservationStore,
    validate_start_receipt,
)

_GIT_ID = re.compile(r"\A[0-9a-f]{40}\Z")
DEFAULT_CADENCE_SECONDS = 900.0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    activation = parser.add_argument_group("explicit activation")
    activation.add_argument(
        "--s2a-enabled",
        action="store_true",
        help="explicitly opt into the reviewed S2A structural prospective path",
    )
    parser.add_argument("--runtime-git-sha", required=True)
    parser.add_argument("--runtime-git-tree", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--start-receipt", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true")
    modes.add_argument("--forever", action="store_true")
    parser.add_argument(
        "--cadence-seconds",
        type=float,
        default=DEFAULT_CADENCE_SECONDS,
        help="continuous collection cadence (default: 900 seconds)",
    )
    return parser


def _checkout_identity(
    root: Path,
    *,
    run_git: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[str, str]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is required for runtime identity")

    def rev_parse(revision: str) -> str:
        result = run_git(
            [git, "rev-parse", revision],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        if not _GIT_ID.fullmatch(value):
            raise RuntimeError(f"git returned malformed identity for {revision}")
        return value

    return rev_parse("HEAD"), rev_parse("HEAD^{tree}")


def _module_path_is_in_root(module: Any, root: Path) -> bool:
    path_value = getattr(module, "__file__", None)
    if not isinstance(path_value, str):
        return False
    try:
        Path(path_value).resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _verify_module_binding(root: Path) -> None:
    if not _module_path_is_in_root(prospective_runner, root):
        raise RuntimeError("prospective runner is outside the executing checkout")
    if not _module_path_is_in_root(prospective_kernel, root):
        raise RuntimeError("prospective kernel is outside the executing checkout")


def _load_receipt(path: Path) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"unable to load start receipt: {path}") from exc
    if not isinstance(receipt, dict):
        raise RuntimeError("start receipt must contain a JSON object")
    validate_start_receipt(receipt)
    return receipt


def _resolve_operational_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    archive = args.archive.resolve()
    store = args.store.resolve()
    receipt = args.start_receipt.resolve()
    if not archive.is_file():
        raise RuntimeError(f"archive does not exist or is not a file: {archive}")
    return archive, store, receipt


def _startup(
    args: argparse.Namespace,
) -> tuple[argparse.Namespace, dict[str, Any], RuntimeIdentity]:
    if not args.s2a_enabled:
        raise RuntimeError("--s2a-enabled is required; refusing legacy-mode startup")
    if args.cadence_seconds != DEFAULT_CADENCE_SECONDS and args.cadence_seconds <= 0:
        raise RuntimeError("--cadence-seconds must be positive")
    actual_sha, actual_tree = _checkout_identity(RUNTIME_ROOT)
    _verify_module_binding(RUNTIME_ROOT)
    if actual_sha != args.runtime_git_sha:
        raise RuntimeError("expected runtime SHA differs from executing checkout HEAD")
    if actual_tree != args.runtime_git_tree:
        raise RuntimeError("expected runtime tree differs from executing checkout tree")
    runtime = RuntimeIdentity(git_sha=actual_sha, git_tree=actual_tree)
    receipt = _load_receipt(args.start_receipt.resolve())
    archive, store_path, receipt_path = _resolve_operational_paths(args)
    args.archive, args.store, args.start_receipt = archive, store_path, receipt_path
    return args, receipt, runtime


def _summary(
    args: argparse.Namespace, receipt: dict[str, Any], runtime: RuntimeIdentity
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": "once" if args.once else "forever",
        "s2a_enabled": True,
        "runtime_git_sha": runtime.git_sha,
        "runtime_git_tree": runtime.git_tree,
        "runtime_root": str(RUNTIME_ROOT),
        "archive": str(args.archive),
        "store": str(args.store),
        "start_receipt": str(args.start_receipt),
        "research_only": True,
        "production_influence": "0",
        "start_receipt_digest": receipt["receipt_digest"],
    }
    if args.forever:
        payload["cadence_seconds"] = args.cadence_seconds
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args, receipt, runtime = _startup(args)
    print(json.dumps(_summary(args, receipt, runtime), sort_keys=True))
    store = ShadowObservationStore(args.store, receipt)
    kwargs = {
        "archive": args.archive,
        "store": store,
        "start_receipt": receipt,
        "runtime": runtime,
        "s2a_enabled": True,
    }
    if args.once:
        cycle_id = f"cycle-{datetime.now(UTC).isoformat()}"
        result = prospective_runner.run_once(cycle_id=cycle_id, **kwargs)
        print(
            json.dumps(
                {
                    "mode": "once",
                    "cycle_id": result.cycle_id,
                    "complete": result.complete,
                    "observations": len(result.observations),
                    "diagnostics": result.diagnostics,
                },
                sort_keys=True,
            )
        )
        return 0 if result.complete else 2
    prospective_runner.run_forever(interval_seconds=args.cadence_seconds, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
