from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.real_time_market_data.orderbook import BookState, SequencedBook
from services.real_time_market_data.session import ConnectionState, RealtimeSupervisor

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_snapshot_delta_complement_sequence_and_staleness() -> None:
    book = SequencedBook("M")
    book.snapshot(10, [["0.40", "2.5"]], [["0.55", "3"]], NOW, "v1")
    view = book.view(NOW)
    assert (
        view.best_yes_bid == Decimal("0.40")
        and view.best_yes_ask == Decimal("0.55")
        and view.state == BookState.CURRENT
    )
    assert book.delta(11, "yes", "0.40", "-0.5", NOW + timedelta(seconds=1)) and book.view(
        NOW
    ).yes_bids[0][1] == Decimal("2.0")
    assert book.view(NOW + timedelta(seconds=40)).state == BookState.STALE
    assert not book.delta(13, "yes", "0.40", "1", NOW) and book.state == BookState.GAP


def test_metadata_change_invalidates_book() -> None:
    book = SequencedBook("M")
    book.snapshot(1, [], [], NOW, "old")
    book.invalidate_for_metadata("new")
    assert book.state == BookState.GAP


class Snapshots:
    def __init__(self) -> None:
        self.calls = []

    def resnapshot(self, ticker: str) -> tuple[int, list[list[str]], list[list[str]], str]:
        self.calls.append(ticker)
        return 5, [["0.4", "1"]], [["0.5", "1"]], "v"


def test_auth_subscribe_reconnect_resnapshot_and_backoff() -> None:
    provider = Snapshots()
    supervisor = RealtimeSupervisor(provider)
    supervisor.subscribe(["B", "A"])
    supervisor.connected(True)
    assert supervisor.state == ConnectionState.HEALTHY and provider.calls == ["A", "B"]
    assert supervisor.disconnected() == 1.0 and all(
        x.state == BookState.GAP for x in supervisor.books.values()
    )
    supervisor.connected(False)
    assert supervisor.state == ConnectionState.DISCONNECTED
    supervisor.sequence_gap("A")
    assert supervisor.state == ConnectionState.HEALTHY and supervisor.books["A"].sequence == 5
