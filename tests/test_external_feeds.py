from datetime import UTC, datetime

import pytest

from services.breaking_signals.adapters import AdapterError, FeedResponse, OfficialFeedAdapter

NOW = datetime(2026, 8, 10, tzinfo=UTC)
RSS = (
    b'<?xml version="1.0"?><rss><channel><item><guid>1</guid>'
    b"<title>Official release</title><link>https://agency.gov/1</link>"
    b"<pubDate>Mon, 10 Aug 2026 00:00:00 GMT</pubDate></item></channel></rss>"
)
ATOM = (
    b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
    b"<id>2</id><title>Atom release</title><updated>2026-08-10T00:00:00Z</updated>"
    b"</entry></feed>"
)


class Transport:
    def __init__(self, responses: list[FeedResponse]):
        self.responses = responses
        self.headers = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
        follow_redirects: bool,
    ) -> FeedResponse:
        self.headers.append(headers)
        assert not follow_redirects
        return self.responses.pop(0)


def test_rss_atom_conditionals_duplicate_and_correction() -> None:
    transport = Transport(
        [
            FeedResponse(
                200, RSS, "application/rss+xml", "etag", "last", "https://agency.gov/feed"
            ),
            FeedResponse(
                304, b"", "application/rss+xml", "etag", "last", "https://agency.gov/feed"
            ),
        ]
    )
    adapter = OfficialFeedAdapter(transport, frozenset({"agency.gov"}))
    items = adapter.fetch("https://agency.gov/feed")
    assert items[0].item_id == "1" and not items[0].corrected
    assert adapter.fetch("https://agency.gov/feed") == [] and transport.headers[1] == {
        "If-None-Match": "etag",
        "If-Modified-Since": "last",
    }
    atom = OfficialFeedAdapter(
        Transport(
            [FeedResponse(200, ATOM, "application/atom+xml", None, None, "https://agency.gov/atom")]
        ),
        frozenset({"agency.gov"}),
    )
    assert atom.fetch("https://agency.gov/atom")[0].item_id == "2"
    changed = RSS.replace(b"Official release", b"Corrected release")
    adapter.transport = Transport(
        [
            FeedResponse(
                200, changed, "application/rss+xml", "etag2", "last2", "https://agency.gov/feed"
            )
        ]
    )
    assert adapter.fetch("https://agency.gov/feed")[0].corrected


def test_feed_ssrf_redirect_malformed_oversized_and_type() -> None:
    response = FeedResponse(200, RSS, "application/rss+xml", None, None, "https://agency.gov/feed")
    for url in ("http://agency.gov/feed", "https://127.0.0.1/feed", "https://evil.example/feed"):
        with pytest.raises(AdapterError):
            OfficialFeedAdapter(
                Transport([response]), frozenset({"agency.gov", "127.0.0.1"})
            ).fetch(url)
    cases = [
        FeedResponse(200, RSS, "application/rss+xml", None, None, "https://other.gov/feed"),
        FeedResponse(200, b"<bad", "application/rss+xml", None, None, "https://agency.gov/feed"),
        FeedResponse(200, b"x" * 20, "application/rss+xml", None, None, "https://agency.gov/feed"),
        FeedResponse(200, RSS, "text/html", None, None, "https://agency.gov/feed"),
    ]
    for item in cases:
        with pytest.raises(AdapterError):
            OfficialFeedAdapter(Transport([item]), frozenset({"agency.gov"}), max_bytes=10).fetch(
                "https://agency.gov/feed"
            )
