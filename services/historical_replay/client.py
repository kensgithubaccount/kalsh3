"""Read-only historical Kalshi clients with complete cursor pagination."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import quote

from services.kalshi_account_gateway.auth import RequestSigner


class HistoricalError(RuntimeError):
    pass


class Transport(Protocol):
    def get(
        self, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class PageManifest:
    endpoint: str
    page_number: int
    cursor_in: str | None
    cursor_out: str | None
    record_count: int


class HistoricalClient:
    def __init__(
        self,
        transport: Transport,
        *,
        signer: RequestSigner | None = None,
        clock_ms: Callable[[], int] = lambda: 0,
        timeout: float = 15,
    ) -> None:
        self.transport = transport
        self.signer = signer
        self.clock_ms = clock_ms
        self.timeout = timeout

    def _headers(self, path: str, authenticated: bool) -> dict[str, str]:
        if not authenticated:
            return {}
        if self.signer is None:
            raise HistoricalError("exact-read credential required for private history")
        return self.signer.headers(self.clock_ms(), "GET", path)

    def iter_pages(
        self, path: str, field: str, *, authenticated: bool = False
    ) -> Iterator[tuple[list[dict[str, Any]], PageManifest]]:
        cursor = None
        seen = set()
        page_number = 0
        seen_ids = set()
        while True:
            if cursor:
                encoded_cursor = quote(cursor, safe="")
                target = path + ("&" if "?" in path else "?") + f"cursor={encoded_cursor}"
            else:
                target = path
            headers = self._headers(target, authenticated)
            try:
                status, payload = self.transport.get(target, headers, timeout_seconds=self.timeout)
            except Exception as exc:
                raise HistoricalError("historical page transport failure") from exc
            if status != 200:
                raise HistoricalError(f"historical page status {status}")
            rows = payload.get(field)
            page_number += 1
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise HistoricalError("historical page malformed")
            for row in rows:
                identity = (
                    row.get("id")
                    or row.get("trade_id")
                    or row.get("fill_id")
                    or row.get("order_id")
                    or row.get("ticker")
                    or row.get("end_period_ts")
                )
                if not isinstance(identity, (str, int)):
                    raise HistoricalError("historical record identity missing")
                normalized_identity = str(identity)
                if normalized_identity in seen_ids:
                    raise HistoricalError("duplicate historical record identity")
                seen_ids.add(normalized_identity)
            next_cursor = payload.get("cursor")
            if next_cursor not in (None, "") and (
                not isinstance(next_cursor, str) or next_cursor in seen
            ):
                raise HistoricalError("historical cursor invalid or repeated")
            yield rows, PageManifest(path, page_number, cursor, next_cursor, len(rows))
            if next_cursor in (None, ""):
                return
            seen.add(next_cursor)
            cursor = next_cursor

    def collect(
        self, path: str, field: str, *, authenticated: bool = False
    ) -> list[dict[str, Any]]:
        output = []
        for rows, _ in self.iter_pages(path, field, authenticated=authenticated):
            output.extend(rows)
        return output

    def _public_object(self, path: str, field: str) -> dict[str, Any]:
        try:
            status, payload = self.transport.get(path, {}, timeout_seconds=self.timeout)
        except Exception as exc:
            raise HistoricalError("historical object transport failure") from exc
        value = payload.get(field)
        if status != 200 or not isinstance(value, dict):
            raise HistoricalError("historical object response malformed")
        return value

    def cutoff(self) -> dict[str, Any]:
        path = "/trade-api/v2/historical/cutoff"
        status, payload = self.transport.get(path, {}, timeout_seconds=self.timeout)
        required = {"market_settled_ts", "trades_created_ts", "orders_updated_ts"}
        if status != 200 or not required.issubset(payload):
            raise HistoricalError("historical cutoff malformed")
        return payload

    def markets(self, *, series_ticker: str | None = None) -> list[dict[str, Any]]:
        path = "/trade-api/v2/historical/markets?limit=1000"
        if series_ticker is None:
            path += "&mve_filter=exclude"
        else:
            if not series_ticker.strip():
                raise HistoricalError("series ticker filter cannot be empty")
            path += f"&series_ticker={quote(series_ticker, safe='')}"
        return self.collect(path, "markets")

    def market(self, ticker: str) -> dict[str, Any]:
        if not ticker.strip():
            raise HistoricalError("historical market ticker cannot be empty")
        target = quote(ticker, safe="")
        return self._public_object(f"/trade-api/v2/historical/markets/{target}", "market")

    def candles(
        self,
        ticker: str,
        interval: int,
        *,
        start_ts: int,
        end_ts: int,
    ) -> list[dict[str, Any]]:
        if interval not in {1, 60, 1440}:
            raise HistoricalError("unsupported candle interval")
        if start_ts < 0 or end_ts < start_ts:
            raise HistoricalError("historical candle time range is invalid")
        if not ticker.strip():
            raise HistoricalError("historical candle ticker cannot be empty")
        target = quote(ticker, safe="")
        path = (
            f"/trade-api/v2/historical/markets/{target}/candlesticks"
            f"?start_ts={start_ts}&end_ts={end_ts}&period_interval={interval}"
        )
        status, payload = self.transport.get(path, {}, timeout_seconds=self.timeout)
        raw_rows = payload.get("candlesticks")
        if status != 200 or not isinstance(raw_rows, list):
            raise HistoricalError("historical candlestick response malformed")
        if any(not isinstance(row, dict) for row in raw_rows):
            raise HistoricalError("historical candlestick response malformed")
        rows = cast(list[dict[str, Any]], raw_rows)
        seen_periods: set[int] = set()
        for candle in rows:
            period = candle.get("end_period_ts")
            if isinstance(period, bool) or not isinstance(period, int):
                raise HistoricalError("historical candlestick period is malformed")
            if period in seen_periods:
                raise HistoricalError("duplicate historical candlestick period")
            seen_periods.add(period)
        return rows

    def trades(self) -> list[dict[str, Any]]:
        return self.collect("/trade-api/v2/historical/trades", "trades")

    def fills(self) -> list[dict[str, Any]]:
        return self.collect(
            "/trade-api/v2/historical/fills?subaccount=0", "fills", authenticated=True
        )

    def orders(self) -> list[dict[str, Any]]:
        return self.collect(
            "/trade-api/v2/historical/orders?subaccount=0", "orders", authenticated=True
        )
