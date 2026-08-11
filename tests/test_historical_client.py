from collections.abc import Mapping
from typing import Any

import pytest

from services.historical_replay.client import HistoricalClient, HistoricalError


class Pages:
    def __init__(self, replies: list[tuple[int, dict[str, Any]]]) -> None:
        self.replies = iter(replies)
        self.paths: list[str] = []

    def get(
        self, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        self.paths.append(path)
        return next(self.replies)


def test_complete_pagination_and_current_cutoff_contract() -> None:
    transport = Pages(
        [
            (200, {"trades": [{"trade_id": "1"}], "cursor": "next"}),
            (200, {"trades": [{"trade_id": "2"}], "cursor": ""}),
        ]
    )
    assert [row["trade_id"] for row in HistoricalClient(transport).trades()] == ["1", "2"]
    assert transport.paths[-1].endswith("cursor=next")
    cutoff = HistoricalClient(
        Pages([(200, {"market_settled_ts": 1, "trades_created_ts": 2, "orders_updated_ts": 3})])
    ).cutoff()
    assert cutoff["orders_updated_ts"] == 3


@pytest.mark.parametrize(
    "replies",
    [
        [
            (200, {"trades": [{"trade_id": "1"}], "cursor": "same"}),
            (200, {"trades": [{"trade_id": "2"}], "cursor": "same"}),
        ],
        [(200, {"trades": [{"trade_id": "1"}], "cursor": "next"}), (503, {})],
        [(200, {"trades": [{"trade_id": "1"}, {"trade_id": "1"}], "cursor": ""})],
    ],
)
def test_partial_repeated_and_duplicate_scans_fail_closed(
    replies: list[tuple[int, dict[str, Any]]],
) -> None:
    with pytest.raises(HistoricalError):
        HistoricalClient(Pages(replies)).trades()


def test_private_history_requires_read_signer_and_no_mutation_api() -> None:
    client = HistoricalClient(Pages([]))
    with pytest.raises(HistoricalError, match="read credential"):
        client.fills()
    assert not any(hasattr(client, name) for name in ("post", "put", "delete", "submit_order"))
    assert client.candles
