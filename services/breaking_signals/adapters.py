"""Credential/cost-gated source adapters; official or authorized paths only, never scraping."""

from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .models import ManipulationFlag, VerificationState


class AdapterError(ValueError):
    pass


class PredictBuddyMode(StrEnum):
    DISABLED = "DISABLED"
    MANUAL_IMPORT = "MANUAL_IMPORT"
    AUTHORIZED_EMAIL = "AUTHORIZED_EMAIL"
    AUTHORIZED_TELEGRAM = "AUTHORIZED_TELEGRAM"
    OFFICIAL_API = "OFFICIAL_API"


@dataclass(frozen=True, slots=True)
class PredictBuddyAlert:
    alert_id: str | None
    original_at: datetime
    wallet: str
    market: str
    vendor_label: str | None
    size: Decimal | None
    raw_hash: str
    ingested_at: datetime
    verification: str = "UNCORROBORATED_VENDOR_SIGNAL"
    vendor: str = "PredictBuddy"


@dataclass(slots=True)
class PredictBuddyAdapter:
    mode: PredictBuddyMode = PredictBuddyMode.DISABLED
    official_api_available: bool = False
    seen: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.mode == PredictBuddyMode.OFFICIAL_API and not self.official_api_available:
            raise AdapterError("PredictBuddy official API is not documented as available")

    def import_authorized(self, payload: dict[str, Any], now: datetime) -> PredictBuddyAlert:
        if self.mode not in {
            PredictBuddyMode.MANUAL_IMPORT,
            PredictBuddyMode.AUTHORIZED_EMAIL,
            PredictBuddyMode.AUTHORIZED_TELEGRAM,
        }:
            raise AdapterError("authorized PredictBuddy import is disabled")
        for key in ("timestamp", "wallet", "market"):
            if not isinstance(payload.get(key), str):
                raise AdapterError("malformed PredictBuddy alert")
        try:
            original = datetime.fromisoformat(
                payload["timestamp"].replace("Z", "+00:00")
            ).astimezone(UTC)
        except ValueError as exc:
            raise AdapterError("malformed PredictBuddy timestamp") from exc
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if digest in self.seen:
            raise AdapterError("duplicate PredictBuddy alert")
        self.seen.add(digest)
        size = None if payload.get("size") is None else Decimal(str(payload["size"]))
        return PredictBuddyAlert(
            payload.get("alert_id"),
            original,
            payload["wallet"],
            payload["market"],
            payload.get("vendor_label"),
            size,
            digest,
            now,
        )

    def corroborate(
        self, alert: PredictBuddyAlert, direct_wallet: str, direct_market: str
    ) -> PredictBuddyAlert:
        from dataclasses import replace

        return (
            replace(alert, verification="DIRECT_MARKET_CORROBORATED")
            if alert.wallet == direct_wallet and alert.market == direct_market
            else alert
        )


class XState(StrEnum):
    SETUP_REQUIRED = "SETUP_REQUIRED"
    DISABLED = "DISABLED"
    CONNECTING = "CONNECTING"
    SHADOW = "SHADOW"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class XRule:
    rule_id: str
    value: str
    tag: str
    version: int


@dataclass(slots=True)
class XFilteredStreamAdapter:
    bearer_token: bytes | None = field(default=None, repr=False)
    monthly_cost: Decimal = Decimal(0)
    cost_approved: bool = False
    rules: dict[str, XRule] = field(default_factory=dict)
    last_message: datetime | None = None
    reconnects: int = 0

    @property
    def state(self) -> XState:
        return (
            XState.SETUP_REQUIRED
            if self.bearer_token is None or not self.cost_approved
            else XState.SHADOW
        )

    def set_rules(self, rules: list[XRule]) -> None:
        self.rules = {rule.rule_id: rule for rule in rules}

    def parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        includes = payload.get("includes", {})
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("id"), str)
            or not isinstance(data.get("author_id"), str)
        ):
            raise AdapterError("malformed X event")
        self.last_message = datetime.now(UTC)
        return {
            "event_id": data["id"],
            "author_stable_id": data["author_id"],
            "text": str(data.get("text", "")),
            "original_event_id": data.get("referenced_tweets", [{}])[0].get("id")
            if data.get("referenced_tweets")
            else None,
            "includes": includes,
        }

    def reconnect_delay(self) -> float:
        self.reconnects += 1
        return float(min(30, 0.5 * 2 ** min(self.reconnects, 6)))


@dataclass(frozen=True, slots=True)
class BlueskyEvent:
    did: str
    handle: str | None
    uri: str
    cid: str | None
    operation: str
    commit_at: datetime
    ingested_at: datetime
    text: str | None
    verification: VerificationState
    flags: tuple[ManipulationFlag, ...]


class CanonicalBlueskyVerifier(Protocol):
    def verify(self, did: str, uri: str, cid: str | None) -> bool: ...


def parse_jetstream(payload: dict[str, Any], now: datetime) -> BlueskyEvent:
    did = payload.get("did")
    commit = payload.get("commit")
    if (
        not isinstance(did, str)
        or not isinstance(commit, dict)
        or commit.get("collection") != "app.bsky.feed.post"
    ):
        raise AdapterError("malformed Bluesky Jetstream event")
    uri = f"at://{did}/{commit.get('collection')}/{commit.get('rkey')}"
    record = commit.get("record", {})
    time_us = payload.get("time_us")
    if not isinstance(time_us, int):
        raise AdapterError("Bluesky time missing")
    return BlueskyEvent(
        did,
        payload.get("handle"),
        uri,
        commit.get("cid"),
        str(commit.get("operation")),
        datetime.fromtimestamp(time_us / 1_000_000, UTC),
        now,
        record.get("text") if isinstance(record, dict) else None,
        VerificationState.UNVERIFIED,
        (ManipulationFlag.UNKNOWN_IDENTITY,),
    )


def verify_bluesky(event: BlueskyEvent, verifier: CanonicalBlueskyVerifier) -> BlueskyEvent:
    from dataclasses import replace

    return (
        replace(event, verification=VerificationState.IDENTITY_VERIFIED, flags=())
        if verifier.verify(event.did, event.uri, event.cid)
        else event
    )


@dataclass(frozen=True, slots=True)
class FeedResponse:
    status: int
    content: bytes
    content_type: str
    etag: str | None
    last_modified: str | None
    final_url: str


class FeedTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
        max_bytes: int,
        follow_redirects: bool,
    ) -> FeedResponse: ...


@dataclass(frozen=True, slots=True)
class FeedItem:
    item_id: str
    title: str
    canonical_url: str | None
    published_at: str | None
    content_hash: str
    corrected: bool


@dataclass(slots=True)
class OfficialFeedAdapter:
    transport: FeedTransport
    allowed_hosts: frozenset[str]
    max_bytes: int = 2_000_000
    timeout_seconds: float = 10
    etag: str | None = None
    last_modified: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)

    def fetch(self, url: str) -> list[FeedItem]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self.allowed_hosts
            or parsed.username
            or parsed.password
        ):
            raise AdapterError("feed URL rejected by allowlist")
        try:
            host = ipaddress.ip_address(parsed.hostname or "")
            if host.is_private or host.is_loopback or host.is_link_local:
                raise AdapterError("feed URL rejected by SSRF policy")
        except ValueError:
            pass
        headers = {}
        if self.etag:
            headers["If-None-Match"] = self.etag
        if self.last_modified:
            headers["If-Modified-Since"] = self.last_modified
        response = self.transport.get(
            url,
            headers=headers,
            timeout_seconds=self.timeout_seconds,
            max_bytes=self.max_bytes,
            follow_redirects=False,
        )
        if response.status == 304:
            return []
        if (
            response.status != 200
            or response.final_url != url
            or len(response.content) > self.max_bytes
        ):
            raise AdapterError("feed response rejected")
        if response.content_type.split(";", 1)[0] not in {
            "application/rss+xml",
            "application/atom+xml",
            "application/xml",
            "text/xml",
        }:
            raise AdapterError("feed content type rejected")
        lowered = response.content.lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise AdapterError("feed XML declarations are not permitted")
        try:
            root = ElementTree.fromstring(response.content)  # noqa: S314 - DTD/entity rejected above
        except ElementTree.ParseError as exc:
            raise AdapterError("malformed feed XML") from exc
        self.etag, self.last_modified = response.etag, response.last_modified
        items = []
        nodes = list(root.findall(".//item")) + list(
            root.findall("{http://www.w3.org/2005/Atom}entry")
        )
        for node in nodes:
            title = (
                node.findtext("title") or node.findtext("{http://www.w3.org/2005/Atom}title") or ""
            )
            guid = node.findtext("guid") or node.findtext("{http://www.w3.org/2005/Atom}id")
            link = node.findtext("link")
            published = node.findtext("pubDate") or node.findtext(
                "{http://www.w3.org/2005/Atom}updated"
            )
            if not guid:
                raise AdapterError("feed item identifier missing")
            digest = hashlib.sha256(ElementTree.tostring(node)).hexdigest()
            corrected = guid in self.hashes and self.hashes[guid] != digest
            self.hashes[guid] = digest
            items.append(FeedItem(guid, title, link, published, digest, corrected))
        return items


@dataclass(frozen=True, slots=True)
class VendorLeadComparison:
    alert_id: str | None
    direct_polymarket_ingest_at: datetime
    predictbuddy_ingest_at: datetime
    difference_ms: int
    first_observed_by: str
    vendor_added_metadata: bool


def compare_predictbuddy(
    alert: PredictBuddyAlert, direct_at: datetime, *, vendor_added_metadata: bool
) -> VendorLeadComparison:
    difference = int((alert.ingested_at - direct_at).total_seconds() * 1000)
    return VendorLeadComparison(
        alert.alert_id,
        direct_at,
        alert.ingested_at,
        difference,
        "direct_polymarket"
        if difference > 0
        else "predictbuddy"
        if difference < 0
        else "simultaneous",
        vendor_added_metadata,
    )
