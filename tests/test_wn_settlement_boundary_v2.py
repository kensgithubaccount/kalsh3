"""Network-denied synthetic boundary probes; no source acquisition or scoring."""

from __future__ import annotations

import ast
import errno
import json
import os
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from services.forecasting import wn_settlement_journal_v2 as journal
from services.forecasting import wn_settlement_safety_v2 as safety

CUTOFF = json.dumps(
    {
        "market_settled_ts": "2026-09-03T00:00:00Z",
        "orders_updated_ts": "2026-10-15T00:00:00Z",
        "trades_created_ts": "2026-09-03T00:00:00Z",
    }
).encode()
HEADERS = b"Content-Type: application/json\r\n"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("NETWORK_DENIED_OFFLINE_TEST")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def sample(ms: int, wall: str = "2026-11-03T00:00:00Z") -> safety.ClockSample:
    return safety.ClockSample(ms * 1_000_000, wall)


def response(status: int = 200, body: bytes = CUTOFF) -> safety.OfflineFrame:
    return safety.OfflineFrame(status, HEADERS, body)


def new_root(tmp_path: Path) -> Path:
    return tmp_path.resolve() / "journal"


def fingerprint(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        p.name: (journal.digest(p.read_bytes()), p.stat().st_mtime_ns, p.stat().st_ino)
        for p in root.iterdir()
    }


def test_exact_85_requests_no_authority_or_custom_url() -> None:
    policy = safety.RequestPolicy(CUTOFF)
    assert len({policy.slot(i).path for i in range(85)}) == 85
    assert len({policy.slot(i).ticker for i in range(1, 85)}) == 84
    for i in range(85):
        policy.check(i, policy.slot(i))
    for i in (-1, 85, True):
        with pytest.raises(safety.BoundaryError):
            policy.slot(i)
    with pytest.raises(safety.BoundaryError):
        safety.RequestPolicy().slot(1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "http://external-api.kalshi.com"),
        ("origin", "https://external-api.kalshi.com.evil.invalid"),
        ("origin", "https://user:pass@external-api.kalshi.com"),
        ("origin", "https://external-api.kalshi.com:443"),
        ("method", "POST"),
        ("method", "HEAD"),
        ("path", "/trade-api/v2/portfolio/balance"),
        ("path", "/trade-api/v2/markets?tickers=anything"),
        ("path", "/trade-api/v2/markets/../portfolio/orders"),
        ("path", "/trade-api/v2/markets/%2e%2e/orders"),
        ("headers", (("Authorization", "forbidden"),)),
        ("headers", (("Cookie", "forbidden"),)),
        ("ticker", "UNFROZEN-TICKER"),
    ],
)
def test_forged_request_rejected(field: str, value: object) -> None:
    policy = safety.RequestPolicy(CUTOFF)
    bad = replace(policy.slot(1), **{field: value})
    with pytest.raises(safety.BoundaryError):
        policy.check(1, bad)


def test_all_tickers_reject_swapped_slots_and_fallback_route() -> None:
    policy = safety.RequestPolicy(CUTOFF)
    for slot in range(1, 85):
        expected = policy.slot(slot)
        alternate = expected.path.replace("/historical/markets/", "/markets/")
        if alternate == expected.path:
            alternate = expected.path.replace("/markets/", "/historical/markets/")
        for bad in (replace(expected, path=alternate), policy.slot(slot % 84 + 1)):
            with pytest.raises(safety.BoundaryError):
                policy.check(slot, bad)


def test_deadlines_never_renew_across_all_phases() -> None:
    model = safety.AbsoluteDeadlineModel(sample(0))
    assert model.begin(sample(0)) == 10_000_000_000
    for phase, ms in zip(model.PHASES, [2000, 4000, 6000, 8000], strict=True):
        assert model.phase(phase, sample(ms)) == (10000 - ms) * 1_000_000
        assert model.request_end_ns == 10_000_000_000
    with pytest.raises(safety.BoundaryError, match="REQUEST_DEADLINE"):
        model.finish(sample(10000))
    with pytest.raises(safety.BoundaryError, match="ALREADY_HALTED"):
        model.begin(sample(10001))


@pytest.mark.parametrize("phase", range(4))
@pytest.mark.parametrize("failure", ["cancel", "deadline"])
def test_cancel_or_deadline_in_every_phase(phase: int, failure: str) -> None:
    model = safety.AbsoluteDeadlineModel(sample(0))
    model.begin(sample(0))
    for name in model.PHASES[:phase]:
        model.phase(name, sample(0))
    with pytest.raises(safety.BoundaryError, match=r"CANCELLED|REQUEST_DEADLINE"):
        model.phase(
            model.PHASES[phase],
            sample(10000 if failure == "deadline" else 0),
            cancelled=failure == "cancel",
        )


def test_cap_pacing_order_single_active_and_total_deadline() -> None:
    model = safety.AbsoluteDeadlineModel(sample(0))
    model.begin(sample(0))
    with pytest.raises(safety.BoundaryError, match="ONE_ACTIVE"):
        model.begin(sample(0))
    with pytest.raises(safety.BoundaryError, match="PHASE_ORDER"):
        model.phase("BODY", sample(0))
    with pytest.raises(safety.BoundaryError, match="BODY_NOT_COMPLETE"):
        model.finish(sample(0))
    for name in model.PHASES:
        model.phase(name, sample(0))
    model.finish(sample(0))
    with pytest.raises(safety.BoundaryError, match="START_PACING"):
        model.begin(sample(2999))
    for i in range(1, 85):
        model.begin(sample(i * 3000))
        for name in model.PHASES:
            model.phase(name, sample(i * 3000))
        model.finish(sample(i * 3000))
    with pytest.raises(safety.BoundaryError, match="REQUEST_CAP"):
        model.begin(sample(85 * 3000))
    near = safety.AbsoluteDeadlineModel(sample(0))
    assert near.begin(sample(899999)) == 900_000_000_000
    with pytest.raises(safety.BoundaryError, match="TOTAL_DEADLINE"):
        near.phase("DNS", sample(900000))


@pytest.mark.parametrize(
    "bad",
    [
        sample(-1),
        sample(0, "2026-11-03T00:00:00"),
        sample(0, "invalid"),
        safety.ClockSample(True, "2026-11-03T00:00:00Z"),
    ],
)
def test_invalid_clocks(bad: safety.ClockSample) -> None:
    with pytest.raises(safety.BoundaryError):
        safety.AbsoluteDeadlineModel(bad)


@pytest.mark.parametrize(
    "bad,reason",
    [(sample(9), "MONOTONIC_ROLLBACK"), (sample(11, "2026-11-02T23:59:59Z"), "WALL_ROLLBACK")],
)
def test_clock_rollback_is_terminal(bad: safety.ClockSample, reason: str) -> None:
    model = safety.AbsoluteDeadlineModel(sample(10))
    model.begin(sample(10))
    with pytest.raises(safety.BoundaryError, match=reason):
        model.phase("DNS", bad)
    assert model.halt_reason == reason


def test_utc_conversion_is_explicit_and_not_synchronization_evidence() -> None:
    assert sample(0, "2026-11-02T19:00:00-05:00").wall() == sample(0).wall()
    assert "does not interrupt" in (safety.AbsoluteDeadlineModel.__doc__ or "")


@pytest.mark.parametrize(
    "frame,reason",
    [
        (response(429), "HTTP_429"),
        (response(403), "HTTP_403"),
        (response(404), "HTTP_404"),
        (safety.OfflineFrame(302, b"Location: https://evil.invalid\r\n", b""), "REDIRECT_REJECTED"),
        (
            safety.OfflineFrame(307, b"Location: /trade-api/v2/portfolio/orders\r\n", b""),
            "REDIRECT_REJECTED",
        ),
        (safety.OfflineFrame(200, b"x" * 16385, b"{}"), "HEADER_LIMIT"),
        (response(body=b"x" * 65537), "BODY_LIMIT_OR_PARTIAL"),
        (replace(response(), complete=False), "BODY_LIMIT_OR_PARTIAL"),
        (safety.OfflineFrame(200, b"x\n: value\r\n", b"{}"), "MALFORMED_HEADER"),
        (safety.OfflineFrame(200, HEADERS + b"x: v\rx\r\n", b"{}"), "MALFORMED_HEADER"),
        (safety.OfflineFrame(200, HEADERS * 2, b"{}"), "DUPLICATE_HEADER"),
        (safety.OfflineFrame(200, b"Content-Type: text/html\r\n", b"{}"), "CONTENT_TYPE"),
        (
            safety.OfflineFrame(200, HEADERS + b"Content-Encoding: gzip\r\n", b"{}"),
            "CONTENT_ENCODING",
        ),
        (
            safety.OfflineFrame(200, HEADERS + b"Transfer-Encoding: chunked\r\n", b"{}"),
            "TRANSFER_ENCODING",
        ),
        (safety.OfflineFrame(200, HEADERS + b"Content-Length: 999\r\n", b"{}"), "CONTENT_LENGTH"),
        (safety.OfflineFrame(None, b"", b"", False, "TLS_FAILURE"), "TLS_FAILURE"),
    ],
)
def test_raw_prefix_bounds_and_negative_reasons(frame: safety.OfflineFrame, reason: str) -> None:
    bounded = safety.bound_frame(frame)
    assert bounded.reason == reason and bounded.simulation_only
    assert bounded.headers == frame.raw_headers[:16384]
    assert bounded.body == frame.raw_body[:65536]


def test_create_once_full_roster_and_read_only_inspection(tmp_path: Path) -> None:
    root = new_root(tmp_path)
    with journal.OfflineJournal.create(root) as writer:
        writer.begin_slot(0)
        writer.record(response())
        writer.begin_slot(1)
        writer.record(response(body=b'{"market":"deliberately unvalidated"}'))
        rows = writer.finalize_negative()
        with pytest.raises(safety.BoundaryError, match="HALTED"):
            writer.begin_slot(2)
    before = fingerprint(root)
    with pytest.raises(FileExistsError):
        journal.OfflineJournal.create(root)
    recovered = journal.inspect_recovery(root)
    assert fingerprint(root) == before
    assert recovered.integrity == "CURRENT_BYTES_VERIFIED_ONLY"
    assert recovered.committed_slots == 2 and len(rows) == len(recovered.rows) == 60
    assert sum(r["capture_state"] == "FAILED" for r in rows) == 5
    assert sum(int(r["depth_abstentions"]) for r in rows) == 59
    assert len({r["target_date"] for r in rows}) == 12
    assert sum(r["outcome_evidence_status"] == "UNVERIFIED" for r in rows) == 14
    assert all(not r["point_score_authorized"] and not r["live_authorized"] for r in rows)
    assert not recovered.acquisition_resume_allowed and not recovered.source_custody_attested
    assert all(p.stat().st_mode & 0o222 == 0 for p in root.iterdir())


@pytest.mark.parametrize("status", [403, 429, 301, 302, 307, 308, 404, 500])
def test_error_raw_bytes_commit_but_no_retry_or_next_slot(tmp_path: Path, status: int) -> None:
    root = new_root(tmp_path)
    with journal.OfflineJournal.create(root) as writer:
        writer.begin_slot(0)
        writer.record(response(status, b"RAW FAILURE BYTES\x00"))
        for slot in (0, 1):
            with pytest.raises(safety.BoundaryError):
                writer.begin_slot(slot)
        rows = writer.finalize_negative()
    raw = (root / "s000.response.sealed").read_bytes()
    assert b"RAW FAILURE BYTES\x00" in raw
    assert all(r["state"] in {"FAILED_CAPTURE", "UNVERIFIED_OFFLINE_ONLY"} for r in rows)
    assert journal.inspect_recovery(root).integrity == "CURRENT_BYTES_VERIFIED_ONLY"


@pytest.mark.parametrize("body", [b"{", b"{}", b"null", b"[]", b"\xff"])
def test_invalid_cutoff_journaled_and_never_routed(tmp_path: Path, body: bytes) -> None:
    root = new_root(tmp_path)
    with journal.OfflineJournal.create(root) as writer:
        writer.begin_slot(0)
        writer.record(response(body=body))
        assert writer.halt_reason == "INVALID_CUTOFF"
        writer.finalize_negative()
    assert not (root / "routing.sealed").exists()


def test_duplicate_missing_out_of_order_slot_and_active_finalize(tmp_path: Path) -> None:
    with journal.OfflineJournal.create(new_root(tmp_path)) as writer:
        for slot in (1, -1, True, 85):
            with pytest.raises(safety.BoundaryError):
                writer.begin_slot(slot)
        writer.begin_slot(0)
        with pytest.raises(safety.BoundaryError):
            writer.begin_slot(0)
        with pytest.raises(safety.BoundaryError):
            writer.finalize_negative()
        writer.record(response())
        with pytest.raises(safety.BoundaryError):
            writer.begin_slot(0)


@pytest.mark.parametrize(
    "fault", ["partial_write", "zero_write", "disk_full", "file_fsync", "dir_fsync", "link_failure"]
)
def test_persistence_faults_poison_writer_and_reserve_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = new_root(tmp_path)
    writer = journal.OfflineJournal.create(root)
    real_write, real_fsync, real_link = os.write, os.fsync, os.link
    calls = 0

    def write(fd: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if fault == "zero_write":
            return 0
        if fault == "disk_full" or (fault == "partial_write" and calls > 1):
            raise OSError(errno.ENOSPC, "SIMULATED_DISK_FULL")
        return real_write(fd, data[:7] if fault == "partial_write" else data)

    def fsync(fd: int) -> None:
        if (
            fault == "dir_fsync" and os.fstat(fd).st_mode & 0o170000 == 0o040000
        ) or fault == "file_fsync":
            raise OSError(errno.EIO, "SIMULATED_FSYNC_FAILURE")
        real_fsync(fd)

    def link(*args: object, **kwargs: object) -> None:
        if fault == "link_failure":
            raise OSError(errno.EIO, "SIMULATED_LINK_FAILURE")
        real_link(*args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as faults:
        faults.setattr(os, "write", write)
        faults.setattr(os, "fsync", fsync)
        faults.setattr(os, "link", link)
        with pytest.raises((OSError, safety.BoundaryError)):
            writer.begin_slot(0)
    with pytest.raises(safety.BoundaryError, match="WRITER_UNAVAILABLE"):
        writer.begin_slot(0)
    writer.close()
    with pytest.raises(FileExistsError):
        journal.OfflineJournal.create(root)
    recovered = journal.inspect_recovery(root)
    assert len(recovered.rows) == 60 and not recovered.acquisition_resume_allowed
    assert not recovered.source_custody_attested


def test_short_writes_are_completed_and_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = os.write
    with journal.OfflineJournal.create(new_root(tmp_path)) as writer:
        monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[:5]))
        writer.finalize_negative()
    assert journal.inspect_recovery(new_root(tmp_path)).integrity == "CURRENT_BYTES_VERIFIED_ONLY"


@pytest.mark.parametrize(
    "mutation",
    ["raw", "commit", "intent", "claim", "ledger", "routing", "extra", "missing", "symlink"],
)
def test_corrupt_or_incomplete_evidence_is_never_admissible(tmp_path: Path, mutation: str) -> None:
    root = new_root(tmp_path)
    with journal.OfflineJournal.create(root) as writer:
        writer.begin_slot(0)
        writer.record(response())
        writer.finalize_negative()
    names = {
        "raw": "s000.response.sealed",
        "commit": "s000.commit.sealed",
        "intent": "s000.intent.sealed",
        "claim": "claim.sealed",
        "ledger": "ledger.sealed",
        "routing": "routing.sealed",
    }
    if mutation in names:
        path = root / names[mutation]
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b"tampered")
    elif mutation == "extra":
        (root / "unexpected").write_bytes(b"x")
    elif mutation == "missing":
        (root / "s000.commit.sealed").unlink()
    else:
        (root / "s000.commit.sealed").unlink()
        (root / "s000.commit.sealed").symlink_to("claim.sealed")
    before = fingerprint(root)
    recovered = journal.inspect_recovery(root)
    assert recovered.integrity == "UNVERIFIED_INCOMPLETE_OR_CORRUPT"
    assert fingerprint(root) == before and len(recovered.rows) == 60


def test_symlink_root_parent_and_replaced_root_rejected(tmp_path: Path) -> None:
    physical = tmp_path.resolve()
    (physical / "alias").symlink_to(physical, target_is_directory=True)
    with pytest.raises(OSError):
        journal.OfflineJournal.create(physical / "alias" / "journal")
    root = new_root(tmp_path)
    with journal.OfflineJournal.create(root) as writer:
        root.rename(physical / "moved")
        root.mkdir()
        with pytest.raises(safety.BoundaryError, match="ROOT_REPLACED"):
            writer.begin_slot(0)


def test_two_process_claim_race_exactly_one_winner(tmp_path: Path) -> None:
    root = new_root(tmp_path)
    code = """import sys
from pathlib import Path
from services.forecasting.wn_settlement_journal_v2 import OfflineJournal
try:
    with OfflineJournal.create(Path(sys.argv[1])) as writer:
        writer.finalize_negative()
except FileExistsError:
    sys.exit(23)
"""
    children = [subprocess.Popen([sys.executable, "-c", code, str(root)]) for _ in range(2)]
    assert sorted(child.wait(timeout=20) for child in children) == [0, 23]
    assert not journal.inspect_recovery(root).acquisition_resume_allowed


@pytest.mark.parametrize("point", ["claim", "intent", "raw_partial", "raw_sealed", "commit_sealed"])
def test_real_process_exit_preserves_negative_recovery(tmp_path: Path, point: str) -> None:
    root = new_root(tmp_path)
    code = """import os,sys
from pathlib import Path
from services.forecasting.wn_settlement_journal_v2 import OfflineJournal
from services.forecasting.wn_settlement_safety_v2 import OfflineFrame
writer=OfflineJournal.create(Path(sys.argv[1]))
point=sys.argv[2]
if point=='claim': os._exit(77)
writer.begin_slot(0)
if point=='intent': os._exit(77)
publish=writer._publish
def interrupted(stem,raw):
    if point=='raw_partial' and stem=='s000.response':
        fd=os.open(str(Path(sys.argv[1])/'s000.response.partial'),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.write(fd,raw[:11]); os.fsync(fd); os._exit(77)
    publish(stem,raw)
    if ((point=='raw_sealed' and stem=='s000.response') or
        (point=='commit_sealed' and stem=='s000.commit')):
        os._exit(77)
writer._publish=interrupted
writer.record(OfflineFrame(429,b'Content-Type: application/json\\r\\n',b'raw failure'))
os._exit(78)
"""
    result = subprocess.run([sys.executable, "-c", code, str(root), point], timeout=20, check=False)
    assert result.returncode == 77
    before = fingerprint(root)
    recovered = journal.inspect_recovery(root)
    assert fingerprint(root) == before and len(recovered.rows) == 60
    assert not recovered.live_authorized and not recovered.acquisition_resume_allowed
    with pytest.raises(FileExistsError):
        journal.OfflineJournal.create(root)


def test_no_live_transport_clock_sampler_runner_or_runtime_imports() -> None:
    forbidden = {
        "requests",
        "httpx",
        "http",
        "urllib",
        "socket",
        "aiohttp",
        "subprocess",
        "asyncio",
        "time",
    }
    for module in (journal, safety):
        source = Path(module.__file__ or "").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & forbidden
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
        assert "__main__" not in source


def test_all_85_slots_and_full_negative_ledger(tmp_path: Path) -> None:
    root = new_root(tmp_path)
    with journal.OfflineJournal.create(root) as writer:
        for slot in range(85):
            request = writer.begin_slot(slot)
            safety.RequestPolicy(CUTOFF).check(slot, request)
            writer.record(response(body=CUTOFF if slot == 0 else b'{"not_source_evidence":true}'))
        with pytest.raises(safety.BoundaryError):
            writer.begin_slot(85)
        writer.finalize_negative()
    result = journal.inspect_recovery(root)
    assert result.integrity == "CURRENT_BYTES_VERIFIED_ONLY" and result.committed_slots == 85
    assert len(result.rows) == 60 and not result.point_score_authorized
    assert len(list(root.iterdir())) == 516


@pytest.mark.parametrize("stem", ["s000.response", "s000.commit", "routing", "ledger"])
def test_failure_at_every_publication_boundary_poisoned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stem: str
) -> None:
    root = new_root(tmp_path)
    writer = journal.OfflineJournal.create(root)
    writer.begin_slot(0)
    real_open = os.open

    def fail_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
        if path == stem + ".partial":
            raise OSError(errno.ENOSPC, "SIMULATED_DISK_FULL")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as faults:
        faults.setattr(os, "open", fail_open)
        with pytest.raises(OSError):
            writer.record(response())
            writer.finalize_negative()
    with pytest.raises(safety.BoundaryError, match="WRITER_UNAVAILABLE"):
        writer.begin_slot(1)
    writer.close()
    result = journal.inspect_recovery(root)
    assert len(result.rows) == 60 and not result.source_custody_attested


def test_parent_fsync_failure_reserves_root_without_returning_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = new_root(tmp_path)

    def fail(_: int) -> None:
        raise OSError(errno.EIO, "SIMULATED_PARENT_FSYNC_FAILURE")

    with monkeypatch.context() as faults:
        faults.setattr(os, "fsync", fail)
        with pytest.raises(OSError):
            journal.OfflineJournal.create(root)
    assert root.is_dir()
    with pytest.raises(FileExistsError):
        journal.OfflineJournal.create(root)
    assert journal.inspect_recovery(root).integrity == "UNVERIFIED_INCOMPLETE_OR_CORRUPT"


def test_invalid_later_clock_and_ambiguous_transport_fail_closed() -> None:
    model = safety.AbsoluteDeadlineModel(sample(0))
    model.begin(sample(0))
    with pytest.raises(safety.BoundaryError, match="INVALID_CLOCK"):
        model.phase("DNS", sample(1, "naive"))
    with pytest.raises(safety.BoundaryError, match="ALREADY_HALTED"):
        model.phase("DNS", sample(2))
    with pytest.raises(safety.BoundaryError, match="AMBIGUOUS_TRANSPORT"):
        safety.bound_frame(replace(response(), transport_failure="TIMEOUT"))


def test_orphan_routing_and_missing_ledger_never_resume(tmp_path: Path) -> None:
    root = new_root(tmp_path)
    with journal.OfflineJournal.create(root):
        pass
    result = journal.inspect_recovery(root)
    assert result.reason == "INCOMPLETE_NO_FINAL_LEDGER" and not result.acquisition_resume_allowed
    (root / "routing.sealed").write_bytes(b"{}")
    assert journal.inspect_recovery(root).integrity == "UNVERIFIED_INCOMPLETE_OR_CORRUPT"
