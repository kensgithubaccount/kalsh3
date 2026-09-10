from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import services.forecasting.gdpnow_parsing as parsing
import services.forecasting.gdpnow_source_acquisition as acquisition


class FakeResponse:
    def __init__(self, *, body: bytes) -> None:
        self.status = 200
        self.body = body

    def read(self, limit: int) -> bytes:
        return self.body

    def getheaders(self) -> list[tuple[str, str]]:
        return []


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        pass

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        pass


def evidence_for(
    body: bytes, monkeypatch: pytest.MonkeyPatch
) -> acquisition.GDPNowAcquisitionEvidence:
    connection = FakeConnection(FakeResponse(body=body))
    monkeypatch.setattr(
        acquisition.http.client,
        "HTTPSConnection",
        lambda host, *, timeout, context: connection,
    )
    return acquisition.acquire_gdpnow_commentary_page()


def entry(
    *,
    header_date: str = "September 3, 2026",
    ordinal: str = "third",
    q_year: str = "2026",
    value: str = "4.7",
    sentence_date: str = "September 3",
    tail: str = " After recent releases, the nowcast changed.",
) -> str:
    return (
        f"<div><h2>{header_date}</h2>\n"
        f"<p>The GDPNow model estimate for real GDP growth (seasonally adjusted "
        f"annual rate) in the {ordinal} quarter of {q_year} is <strong>{value} "
        f"percent</strong> on {sentence_date}, <strong>down from 4.8 percent</strong> "
        f"on September 1.{tail}</p></div>\n"
    )


def page(*entries: str, chrome: bool = True) -> bytes:
    header = (
        "<!doctype html><html><body><nav>Sign up and save 50% off our newsletter!</nav>"
        if chrome
        else "<!doctype html><html><body>"
    )
    return (header + "".join(entries) + "</body></html>").encode("utf-8")


def test_well_formed_representative_page_parses_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = evidence_for(page(entry()), monkeypatch)
    vintage = parsing.parse_gdpnow_commentary(evidence)
    assert vintage.target_quarter == "2026-Q3"
    assert vintage.gdpnow_value == Decimal("4.7")
    assert vintage.publisher_stated_date == date(2026, 9, 3)
    assert vintage.parser_version == parsing.PARSER_VERSION
    assert vintage.acquisition_evidence_id == evidence.content_hash
    parsing.validate_parsed_gdpnow_vintage(vintage)


def test_same_bytes_parsed_twice_yield_exact_same_result(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = evidence_for(page(entry()), monkeypatch)
    first = parsing.parse_gdpnow_commentary(evidence)
    second = parsing.parse_gdpnow_commentary(evidence)
    assert first.target_quarter == second.target_quarter
    assert first.gdpnow_value == second.gdpnow_value
    assert first.publisher_stated_date == second.publisher_stated_date
    assert first.vintage_id == second.vintage_id


def test_negative_growth_value_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = evidence_for(page(entry(value="-1.2")), monkeypatch)
    vintage = parsing.parse_gdpnow_commentary(evidence)
    assert vintage.gdpnow_value == Decimal("-1.2")


def test_html_whitespace_variation_still_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = (
        b"<!doctype html><html><body><div>\n\n    <h2>September 3, 2026</h2>\n"
        b"<p>The GDPNow model estimate for real GDP growth (seasonally adjusted "
        b"annual rate) in the third quarter of 2026 is <strong>4.7 percent</strong> "
        b"on September 3, <strong>down from 4.8 percent</strong> on September 1. "
        b"After recent releases, the nowcast changed.</p>\n\n</div></body></html>"
    )
    evidence = evidence_for(raw, monkeypatch)
    vintage = parsing.parse_gdpnow_commentary(evidence)
    assert vintage.gdpnow_value == Decimal("4.7")


def test_page_chrome_with_unrelated_percentages_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = evidence_for(page(entry(), chrome=True), monkeypatch)
    vintage = parsing.parse_gdpnow_commentary(evidence)
    assert vintage.gdpnow_value == Decimal("4.7")


@pytest.mark.parametrize(
    "body_factory",
    [
        lambda: page(
            "<div><h2>September 3, 2026</h2><p>The GDPNow model estimate for real GDP "
            "growth (seasonally adjusted annual rate) in the third quarter of 2026 is "
            "unavailable this week.</p></div>"
        ),
        lambda: page(
            "<div><h2>September 3, 2026</h2><p>The economy grew by <strong>4.7 "
            "percent</strong> on September 3.</p></div>"
        ),
        lambda: page(
            "<div><p>The GDPNow model estimate for real GDP growth (seasonally "
            "adjusted annual rate) in the third quarter of 2026 is <strong>4.7 "
            "percent</strong> on September 3, <strong>down from 4.8 percent</strong> "
            "on September 1.</p></div>"
        ),
        lambda: page(
            "<div><h2>September 3, 2026</h2><p>The GDPNow model estimate for real GDP "
            "growth (seasonally adjusted annual rate) is <strong>4.7 percent</strong> "
            "on September 3.</p></div>"
        ),
        lambda: page(""),
    ],
)
def test_missing_required_elements_fail_closed(
    body_factory: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = evidence_for(body_factory(), monkeypatch)  # type: ignore[operator]
    with pytest.raises(parsing.GDPNowParsingError):
        parsing.parse_gdpnow_commentary(evidence)


def test_malformed_calendar_date_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = evidence_for(
        page(entry(header_date="February 30, 2026", sentence_date="February 30")), monkeypatch
    )
    with pytest.raises(parsing.GDPNowParsingError, match="malformed"):
        parsing.parse_gdpnow_commentary(evidence)


def test_header_and_sentence_date_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = evidence_for(
        page(entry(header_date="September 3, 2026", sentence_date="September 5")), monkeypatch
    )
    with pytest.raises(parsing.GDPNowParsingError, match="does not match"):
        parsing.parse_gdpnow_commentary(evidence)


def test_older_entry_only_value_does_not_leak_through_ambiguous_newest_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The topmost header has no parseable sentence; an older entry does. This
    must fail closed rather than silently reporting the older entry's value."""
    malformed_newest = (
        "<div><h2>September 10, 2026</h2><p>The GDPNow model estimate is not yet "
        "available for this release.</p></div>\n"
    )
    older_valid = entry(header_date="September 3, 2026", sentence_date="September 3")
    evidence = evidence_for(page(malformed_newest, older_valid), monkeypatch)
    with pytest.raises(parsing.GDPNowParsingError, match="ambiguous"):
        parsing.parse_gdpnow_commentary(evidence)


def test_multiple_quarters_in_prose_does_not_confuse_anchored_grammar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tail = (
        " The first estimate of second-quarter real GDP growth will be released "
        "later. The nowcast of fourth-quarter growth is not yet available."
    )
    evidence = evidence_for(page(entry(tail=tail)), monkeypatch)
    vintage = parsing.parse_gdpnow_commentary(evidence)
    assert vintage.target_quarter == "2026-Q3"


def test_non_utf8_body_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = evidence_for(b"\xff\xfe not valid utf-8 html", monkeypatch)
    with pytest.raises(parsing.GDPNowParsingError, match="UTF-8"):
        parsing.parse_gdpnow_commentary(evidence)


def test_parser_revalidates_acquisition_evidence_rather_than_trusting_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = evidence_for(page(entry()), monkeypatch)
    object.__setattr__(evidence, "raw_body", b"mutated, never re-acquired")
    with pytest.raises(acquisition.GDPNowAcquisitionError):
        parsing.parse_gdpnow_commentary(evidence)


def test_parsed_vintage_cannot_be_constructed_without_parser_capability() -> None:
    """Part G #6: correct-looking parsed values without positive acquisition
    evidence cannot become canonical parsed authority."""
    with pytest.raises(parsing.GDPNowParsingError, match="capability"):
        parsing.ParsedGDPNowVintage(
            target_quarter="2026-Q3",
            gdpnow_value=Decimal("4.7"),
            publisher_stated_date=date(2026, 9, 3),
            acquisition_evidence_id="forged-evidence-id",
        )


def test_forged_parsed_vintage_object_fails_revalidation() -> None:
    forged = object.__new__(parsing.ParsedGDPNowVintage)
    with pytest.raises((AttributeError, parsing.GDPNowParsingError)):
        parsing.validate_parsed_gdpnow_vintage(forged)


def test_reconstructed_vintage_with_recomputed_hash_still_fails_issuance_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = evidence_for(page(entry()), monkeypatch)
    vintage = parsing.parse_gdpnow_commentary(evidence)
    old_value = vintage.gdpnow_value
    old_id = vintage.vintage_id
    try:
        object.__setattr__(vintage, "gdpnow_value", Decimal("9.9"))
        redigest = parsing._vintage_digest_values(
            {
                "target_quarter": vintage.target_quarter,
                "gdpnow_value": vintage.gdpnow_value,
                "publisher_stated_date": vintage.publisher_stated_date,
                "parser_version": vintage.parser_version,
                "acquisition_evidence_id": vintage.acquisition_evidence_id,
            }
        )
        object.__setattr__(vintage, "vintage_id", redigest)
        with pytest.raises(parsing.GDPNowParsingError, match="unissued"):
            parsing.validate_parsed_gdpnow_vintage(vintage)
    finally:
        object.__setattr__(vintage, "gdpnow_value", old_value)
        object.__setattr__(vintage, "vintage_id", old_id)
    parsing.validate_parsed_gdpnow_vintage(vintage)


def test_classify_new_update_requires_forecast_tuple_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    older = evidence_for(page(entry(value="4.7")), monkeypatch)
    newer = evidence_for(
        page(entry(header_date="September 5, 2026", sentence_date="September 5", value="4.9")),
        monkeypatch,
    )
    previous = parsing.GDPNowCapture(
        parsed=parsing.parse_gdpnow_commentary(older), raw_body_sha256=older.raw_body_sha256
    )
    current = parsing.GDPNowCapture(
        parsed=parsing.parse_gdpnow_commentary(newer), raw_body_sha256=newer.raw_body_sha256
    )
    assert (
        parsing.classify_gdpnow_update(previous=previous, current=current)
        == parsing.UpdateClassification.NEW_UPDATE
    )


def test_classify_unchanged_forecast_despite_raw_byte_difference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw-byte difference alone must never be treated as a new forecast."""
    first = evidence_for(page(entry(tail=" Original trailing prose.")), monkeypatch)
    second = evidence_for(
        page(entry(tail=" Different trailing prose, same forecast.")), monkeypatch
    )
    assert first.raw_body_sha256 != second.raw_body_sha256
    previous = parsing.GDPNowCapture(
        parsed=parsing.parse_gdpnow_commentary(first), raw_body_sha256=first.raw_body_sha256
    )
    current = parsing.GDPNowCapture(
        parsed=parsing.parse_gdpnow_commentary(second), raw_body_sha256=second.raw_body_sha256
    )
    assert (
        parsing.classify_gdpnow_update(previous=previous, current=current)
        == parsing.UpdateClassification.UNCHANGED_FORECAST
    )


def test_classify_page_changed_non_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    good = evidence_for(page(entry()), monkeypatch)
    broken = evidence_for(
        page("<div><h2>September 10, 2026</h2><p>Unavailable.</p></div>"), monkeypatch
    )
    previous = parsing.GDPNowCapture(
        parsed=parsing.parse_gdpnow_commentary(good), raw_body_sha256=good.raw_body_sha256
    )
    current = parsing.GDPNowCapture(parsed=None, raw_body_sha256=broken.raw_body_sha256)
    assert (
        parsing.classify_gdpnow_update(previous=previous, current=current)
        == parsing.UpdateClassification.PAGE_CHANGED_NON_FORECAST
    )


def test_classify_malformed_or_unknown_when_bytes_unchanged_and_unparseable() -> None:
    stuck = parsing.GDPNowCapture(parsed=None, raw_body_sha256="same-hash")
    current = parsing.GDPNowCapture(parsed=None, raw_body_sha256="same-hash")
    assert (
        parsing.classify_gdpnow_update(previous=stuck, current=current)
        == parsing.UpdateClassification.MALFORMED_OR_UNKNOWN
    )


def test_classify_malformed_or_unknown_with_no_previous_and_unparseable_current() -> None:
    current = parsing.GDPNowCapture(parsed=None, raw_body_sha256="only-hash")
    assert (
        parsing.classify_gdpnow_update(previous=None, current=current)
        == parsing.UpdateClassification.MALFORMED_OR_UNKNOWN
    )


def test_classify_new_update_with_no_previous_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = evidence_for(page(entry()), monkeypatch)
    current = parsing.GDPNowCapture(
        parsed=parsing.parse_gdpnow_commentary(evidence), raw_body_sha256=evidence.raw_body_sha256
    )
    assert (
        parsing.classify_gdpnow_update(previous=None, current=current)
        == parsing.UpdateClassification.NEW_UPDATE
    )
