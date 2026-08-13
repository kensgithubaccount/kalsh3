"""Accessible, server-rendered SVG chart primitives for the Control Center.

No JavaScript, no client-side chart library, no CDN. Every chart is built
from real values the caller already validated (never fabricated), exposes an
openable data table as its exact text equivalent, and always shows values as
text alongside color so nothing depends on color alone.
"""

from __future__ import annotations

import html
from decimal import Decimal

from .product import dollars

# Tuned for the dark trading-terminal background (M23B): light enough to read
# clearly on near-black, still restrained rather than neon/saturated.
_PALETTE = ("#3ecf8e", "#e0a94a", "#7fb8e8", "#b18cf0")
_LINE_COLOR = "#3ecf8e"


def chart_empty_state(message: str) -> str:
    return f'<div class="chart-empty" role="note">{html.escape(message)}</div>'


def _data_table(caption: str, headers: tuple[str, str], rows: list[tuple[str, str]]) -> str:
    """Render the chart's exact values as a table any viewer can open.

    Wrapped in native <details> (not visually-hidden) so it helps every
    reader who cannot parse the SVG well — screen-reader users, low-vision
    and colorblind users, and anyone who just wants the exact numbers.
    """
    body = "".join(
        f"<tr><th scope=row>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in rows
    )
    table = (
        f'<table class="chart-data"><caption>{html.escape(caption)}</caption>'
        f"<thead><tr><th scope=col>{html.escape(headers[0])}</th>"
        f"<th scope=col>{html.escape(headers[1])}</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )
    return f"<details class=chart-table><summary>Exact values</summary>{table}</details>"


def composition_bar(title: str, segments: list[tuple[str, Decimal]]) -> str:
    """Stacked horizontal bar for a small set of real, already-reconciled amounts."""
    positive = [(label, value) for label, value in segments if value > 0]
    total = sum((value for _, value in positive), Decimal(0))
    if total <= 0 or not positive:
        return chart_empty_state(f"{title}: insufficient reconciled data to visualize.")
    width, height = 640.0, 48
    x = 0.0
    bars, legend = [], []
    for index, (label, value) in enumerate(positive):
        share = value / total
        segment_width = float(share) * width
        color = _PALETTE[index % len(_PALETTE)]
        percent = f"{float(share) * 100:.1f}%"
        formatted = dollars(value)
        bars.append(
            f'<rect x="{x:.2f}" y="0" width="{segment_width:.2f}" height="{height}" fill="{color}">'
            f"<title>{html.escape(label)}: {html.escape(formatted)} ({percent})</title></rect>"
        )
        legend.append(
            f'<li><span class="chart-swatch chart-swatch-{index % len(_PALETTE)}" '
            'aria-hidden="true"></span>'
            f"{html.escape(label)}: <strong>{html.escape(formatted)}</strong> ({percent})</li>"
        )
        x += segment_width
    svg = (
        f'<svg role="img" aria-label="{html.escape(title)} composition chart" '
        f'viewBox="0 0 {width:.0f} {height}" class="chart-bar" preserveAspectRatio="none">'
        + "".join(bars)
        + "</svg>"
    )
    table = _data_table(
        title, ("Segment", "Value"), [(label, dollars(value)) for label, value in positive]
    )
    return (
        f'<figure class="chart">{svg}<figcaption><ul class="chart-legend">'
        + "".join(legend)
        + f"</ul></figcaption></figure>{table}"
    )


def limit_bars(title: str, entries: list[tuple[str, Decimal]]) -> str:
    """Horizontal bars comparing configured policy limits on one shared scale.

    These are the policy ceilings themselves, not current usage against them;
    usage is not charted here unless it is real and reconciled.

    Bar width is an SVG `width` attribute, not a CSS `style="width:...%"`
    inline style — this page's CSP is `style-src 'self'` with no
    'unsafe-inline', which silently drops inline style attributes (every bar
    would render at its CSS default width instead of its real value). SVG
    presentation attributes are unaffected by style-src.
    """
    if not entries:
        return chart_empty_state(f"{title}: no policy limits configured.")
    maximum = max(value for _, value in entries)
    scale = maximum if maximum > 0 else Decimal(1)
    bar_width, bar_height = 200.0, 14.0
    rows = []
    for label, value in entries:
        share = float(value / scale) if scale > 0 else 0.0
        fill_width = max(share * bar_width, 1.0) if value > 0 else 0.0
        svg = (
            f'<svg viewBox="0 0 {bar_width:.0f} {bar_height:.0f}" class="limit-bar" '
            'role="presentation" aria-hidden="true" preserveAspectRatio="none">'
            f'<rect class="limit-track" x="0" y="0" width="{bar_width:.0f}" '
            f'height="{bar_height:.0f}" />'
            f'<rect class="limit-fill" x="0" y="0" width="{fill_width:.2f}" '
            f'height="{bar_height:.0f}" />'
            "</svg>"
        )
        rows.append(
            f'<div class="limit-row"><span class="limit-label">{html.escape(label)}</span>'
            f"{svg}"
            f'<span class="limit-value">{html.escape(dollars(value))}</span></div>'
        )
    table = _data_table(
        title, ("Limit", "Value"), [(label, dollars(value)) for label, value in entries]
    )
    return (
        f'<div class="chart limit-chart" role="img" aria-label="{html.escape(title)} bar chart">'
        + "".join(rows)
        + f"</div>{table}"
    )


def sparkline(title: str, points: list[tuple[str, Decimal]]) -> str:
    """Line chart of real persisted account snapshots; requires at least two points."""
    if len(points) < 2:
        return chart_empty_state(
            f"{title}: insufficient history to chart. This accumulates automatically after "
            "each successful read-only account reconciliation."
        )
    values = [value for _, value in points]
    minimum, maximum = min(values), max(values)
    span = maximum - minimum
    width, height, pad = 640.0, 120.0, 8.0
    step = (width - 2 * pad) / (len(points) - 1)

    def y_of(value: Decimal) -> float:
        if span == 0:
            return height / 2
        return height - pad - (float((value - minimum) / span) * (height - 2 * pad))

    coords = [(pad + index * step, y_of(value)) for index, (_, value) in enumerate(points)]
    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in coords)
    first_label, first_value = points[0]
    last_label, last_value = points[-1]
    summary = (
        f"{title} from {first_label} ({dollars(first_value)}) to "
        f"{last_label} ({dollars(last_value)}); range {dollars(minimum)} to {dollars(maximum)}."
    )
    last_x, last_y = coords[-1]
    svg = (
        f'<svg role="img" aria-label="{html.escape(summary)}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" class="chart-sparkline" '
        f'preserveAspectRatio="none">'
        f'<polyline points="{polyline}" fill="none" stroke="{_LINE_COLOR}" stroke-width="2.5" />'
        f'<circle cx="{last_x:.2f}" cy="{last_y:.2f}" r="3.5" fill="{_LINE_COLOR}" />'
        "</svg>"
    )
    table = _data_table(
        title, ("Observed at", "Value"), [(label, dollars(value)) for label, value in points]
    )
    return (
        f'<figure class="chart">{svg}'
        f'<figcaption class="chart-caption">{html.escape(summary)}</figcaption></figure>{table}'
    )
