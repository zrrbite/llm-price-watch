"""Render the static site: index.html and an Atom feed.

Written by hand rather than with a static-site generator. The whole page is a
few hundred lines of string building, which keeps it readable in one sitting
and removes an entire class of dependency-upgrade breakage from a repo whose
only job is to still be working in a year.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import diffing

SITE_TITLE = "LLM price watch"
SITE_TAGLINE = "What changed in Anthropic and GitHub Copilot pricing — not just what it costs."
SITE_URL = "https://zrrbite.github.io/llm-price-watch/"

CLASS_LABELS = {
    diffing.PRICE_CHANGED: "price",
    diffing.MODEL_ADDED: "added",
    diffing.MODEL_REMOVED: "removed",
    diffing.DETAIL_CHANGED: "detail",
    diffing.ADVISORY_ADDED: "note",
    diffing.ADVISORY_CHANGED: "note changed",
    diffing.ADVISORY_REMOVED: "note withdrawn",
}

ADVISORY_CLASSES = {
    diffing.ADVISORY_ADDED,
    diffing.ADVISORY_CHANGED,
    diffing.ADVISORY_REMOVED,
}

VENDOR_OF = {
    "anthropic": "Anthropic",
    "copilot-pricing": "Copilot",
    "copilot-multipliers": "Copilot",
    "copilot-deprecations": "Copilot",
}

SOURCE_LABELS = {
    "anthropic": "Anthropic API",
    "copilot-pricing": "Copilot tokens",
    "copilot-multipliers": "Copilot multipliers",
    "copilot-deprecations": "Copilot retirements",
}

# Column order per source, so the reference tables read the way the vendor's
# own page does rather than in dict order.
COLUMNS = {
    "anthropic": [
        ("input", "Input"),
        ("output", "Output"),
        ("cache_read", "Cache read"),
        ("cache_write_5m", "Cache write 5m"),
        ("cache_write_1h", "Cache write 1h"),
        ("batch_input", "Batch in"),
        ("batch_output", "Batch out"),
        ("status", "Status"),
    ],
    "copilot-pricing": [
        ("provider", "Provider"),
        ("input", "Input"),
        ("cached_input", "Cached in"),
        ("output", "Output"),
        ("cache_write", "Cache write"),
        ("release_status", "Status"),
        ("category", "Category"),
    ],
    "copilot-multipliers": [("multiplier", "Multiplier")],
    "copilot-deprecations": [
        ("retirement_date", "Retires"),
        ("suggested_alternative", "Replacement"),
    ],
}

CSS = """
:root {
  --bg: #fbfbfa; --fg: #1a1a19; --muted: #6b6b66; --line: #e2e2dd;
  --card: #ffffff; --accent: #a3502a; --up: #a32a2a; --down: #1f7a4d;
  --chip: #f0f0ec;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16161a; --fg: #e8e8e4; --muted: #9a9a92; --line: #2c2c33;
    --card: #1d1d22; --accent: #e0915f; --up: #f08a7a; --down: #6ed49f;
    --chip: #26262c;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.6 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 900px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
header { border-bottom: 1px solid var(--line); padding-bottom: 1.5rem; margin-bottom: 2rem; }
h1 { font-size: 1.6rem; margin: 0 0 .35rem; letter-spacing: -0.01em; }
.tagline { color: var(--muted); margin: 0 0 1rem; }
h2 {
  font-size: .78rem; text-transform: uppercase; letter-spacing: .09em;
  color: var(--muted); margin: 2.75rem 0 1rem; font-weight: 600;
}
.status { font-size: .85rem; color: var(--muted); }
.status .ok { color: var(--down); }
.status .bad { color: var(--up); font-weight: 600; }

.filters { display: flex; gap: .5rem; flex-wrap: wrap; margin: 0 0 1.25rem; }
.filters button {
  font: inherit; font-size: .82rem; padding: .3rem .7rem; cursor: pointer;
  border: 1px solid var(--line); background: var(--card); color: var(--muted);
  border-radius: 999px;
}
.filters button[aria-pressed="true"] { color: var(--fg); border-color: var(--accent); }

.day { margin-bottom: 1.75rem; }
.day > time {
  display: block; font-variant-numeric: tabular-nums; font-weight: 600;
  font-size: .9rem; margin-bottom: .6rem; color: var(--muted);
}
.entry {
  border: 1px solid var(--line); border-left: 3px solid var(--line);
  background: var(--card); border-radius: 4px;
  padding: .7rem .9rem; margin-bottom: .5rem;
}
.entry.price_changed { border-left-color: var(--accent); }
.entry.model_added { border-left-color: var(--down); }
.entry.model_removed { border-left-color: var(--up); }
.entry .meta { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; margin-bottom: .3rem; }
.chip {
  font-size: .68rem; text-transform: uppercase; letter-spacing: .06em;
  background: var(--chip); color: var(--muted); padding: .12rem .45rem; border-radius: 3px;
}
.entry .summary { font-size: .93rem; }
.entry .summary code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .87em; font-variant-numeric: tabular-nums;
}
.entry .note { font-size: .82rem; color: var(--muted); margin-top: .3rem; }
.entry a { color: var(--accent); }
.advisory .summary { font-size: .9rem; }

.tablewrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 4px; background: var(--card); }
table { border-collapse: collapse; width: 100%; font-size: .86rem; }
th, td { text-align: right; padding: .45rem .7rem; white-space: nowrap; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; position: sticky; left: 0; background: var(--card); }
thead th { font-weight: 600; color: var(--muted); font-size: .74rem; text-transform: uppercase; letter-spacing: .05em; }
tbody tr:last-child td { border-bottom: none; }
td.num { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
caption { text-align: left; padding: .6rem .7rem; font-size: .82rem; color: var(--muted); border-bottom: 1px solid var(--line); }
caption a { color: var(--accent); }

nav.jump { display: flex; gap: .9rem; flex-wrap: wrap; margin: .75rem 0 0; font-size: .85rem; }
nav.jump a { color: var(--accent); text-decoration: none; }
nav.jump a:hover { text-decoration: underline; }

.cards { display: grid; gap: .6rem; grid-template-columns: 1fr; }
@media (min-width: 660px) { .cards.two { grid-template-columns: 1fr 1fr; } }
.card {
  border: 1px solid var(--line); border-left: 3px solid var(--accent);
  background: var(--card); border-radius: 4px; padding: .8rem .95rem;
}
.card.urgent { border-left-color: var(--up); }
.card.calm { border-left-color: var(--down); }
.card h3 { margin: 0 0 .3rem; font-size: .97rem; }
.card .models { font-size: .88rem; margin-bottom: .35rem; }
.mono, .card .models code, .price {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-variant-numeric: tabular-nums; font-size: .87em;
}
.card .models code { background: var(--chip); padding: .05rem .32rem; border-radius: 3px; }
.card p { margin: .35rem 0 0; font-size: .86rem; color: var(--muted); }
.card .why { color: var(--fg); opacity: .85; }
.deadline { font-size: .72rem; text-transform: uppercase; letter-spacing: .07em; font-weight: 700; }
.deadline.urgent { color: var(--up); }
.deadline.calm { color: var(--muted); }
.stale { color: var(--up); font-weight: 600; }
.note-inline { font-size: .83rem; color: var(--muted); margin: -.4rem 0 1rem; }

footer { margin-top: 3.5rem; padding-top: 1.25rem; border-top: 1px solid var(--line); font-size: .82rem; color: var(--muted); }
footer a { color: var(--accent); }
footer p { margin: .4rem 0; }
.empty { color: var(--muted); font-size: .9rem; font-style: italic; }
"""

FILTER_JS = """
(function () {
  var buttons = document.querySelectorAll('.filters button');
  function apply(vendor) {
    document.querySelectorAll('[data-vendor]').forEach(function (el) {
      el.hidden = vendor !== 'all' && el.dataset.vendor !== vendor;
    });
    document.querySelectorAll('.day').forEach(function (day) {
      var any = Array.prototype.some.call(day.querySelectorAll('.entry'), function (e) { return !e.hidden; });
      day.hidden = !any;
    });
  }
  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      buttons.forEach(function (o) { o.setAttribute('aria-pressed', String(o === b)); });
      apply(b.dataset.filter);
    });
  });
})();
"""


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def _fields_html(entry: dict) -> str:
    fields = entry.get("fields") or []
    if not fields:
        return esc(entry.get("summary", ""))
    key = esc(entry.get("key", ""))
    parts = []
    for field in fields:
        name = field.get("field", "")
        old = diffing.fmt_value(field.get("old"), name)
        new = diffing.fmt_value(field.get("new"), name)
        parts.append(f"{esc(name)} <code>{esc(old)}</code> → <code>{esc(new)}</code>")
    return f"<strong>{key}</strong> — " + ", ".join(parts)


def render_entry(entry: dict) -> str:
    cls = entry.get("class", "")
    vendor = VENDOR_OF.get(entry.get("source", ""), "?")
    label = CLASS_LABELS.get(cls, cls)
    source_label = SOURCE_LABELS.get(entry.get("source", ""), entry.get("source", ""))

    if cls in ADVISORY_CLASSES:
        body = esc(entry.get("text") or entry.get("summary", ""))
        extra = " advisory"
    else:
        body = _fields_html(entry)
        extra = ""

    bits = [
        f'<div class="entry {esc(cls)}{extra}" data-vendor="{esc(vendor)}">',
        '<div class="meta">',
        f'<span class="chip">{esc(vendor)}</span>',
        f'<span class="chip">{esc(source_label)}</span>',
        f'<span class="chip">{esc(label)}</span>',
        "</div>",
        f'<div class="summary">{body}</div>',
    ]
    note = entry.get("note")
    ref = entry.get("ref")
    if note or ref:
        piece = f"{esc(note)} " if note else ""
        if ref:
            piece += f'<a href="{esc(ref)}">source</a>'
        bits.append(f'<div class="note">{piece}</div>')
    bits.append("</div>")
    return "".join(bits)


def render_changelog(entries: list[dict], limit_days: int | None = None) -> str:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_date[entry.get("date", "unknown")].append(entry)

    dates = sorted(by_date, reverse=True)
    if limit_days:
        dates = dates[:limit_days]
    if not dates:
        return '<p class="empty">Nothing recorded yet.</p>'

    out = []
    for date in dates:
        rows = "".join(render_entry(e) for e in by_date[date])
        out.append(f'<section class="day"><time datetime="{esc(date)}">{esc(date)}</time>{rows}</section>')
    return "".join(out)


def render_table(snapshot: dict) -> str:
    source = snapshot.get("source", "")
    columns = COLUMNS.get(source) or []
    rows = snapshot.get("rows", [])
    if not rows:
        return ""

    present = [(f, label) for f, label in columns if any(f in r.get("values", {}) for r in rows)]
    head = "".join(f"<th>{esc(label)}</th>" for _, label in present)

    body = []
    for row in rows:
        values = row.get("values", {})
        cells = [f"<td>{esc(row.get('key', ''))}</td>"]
        for field, _ in present:
            value = values.get(field)
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            text = diffing.fmt_value(value, field) if value is not None else "—"
            cells.append(f'<td class="{"num" if numeric else ""}">{esc(text)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")

    vendor = VENDOR_OF.get(source, "")
    caption = (
        f'<caption>{esc(snapshot.get("title", source))} · '
        f'<a href="{esc(snapshot.get("url", ""))}">source</a> · '
        f'fetched {esc(snapshot.get("fetched", "")[:10])}</caption>'
    )
    return (
        f'<div class="tablewrap" data-vendor="{esc(vendor)}"><table>{caption}'
        f"<thead><tr><th>Model</th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _price_pair(values: dict) -> str:
    inp = diffing.fmt_value(values.get("input"), "input") if values.get("input") is not None else None
    out = diffing.fmt_value(values.get("output"), "output") if values.get("output") is not None else None
    if inp and out:
        return f'<span class="price">{esc(inp)} / {esc(out)}</span> per MTok'
    return ""


def render_offers(offers: list[dict]) -> str:
    """Everything currently cheaper than usual, soonest deadline first."""
    if not offers:
        return '<p class="empty">Nothing on offer that either vendor has written down.</p>'

    cards = []
    for offer in offers:
        days = offer.get("days_left")
        if days is not None and days <= 7:
            tone, when = "urgent", ("ends today" if days == 0 else f"{days} day{'s' if days != 1 else ''} left")
        elif days is not None:
            tone, when = "", f"{days} days left · until {offer.get('expires')}"
        else:
            tone, when = "calm", "no stated end date"

        models = offer.get("models") or []
        model_html = " ".join(f"<code>{esc(m)}</code>" for m in models)
        heading = "Promotional pricing" if offer.get("kind") == "promotion" else "Price cut, still in force"

        cards.append(
            f'<div class="card {tone}" data-vendor="{esc(offer.get("vendor", ""))}">'
            f'<div class="meta"><span class="chip">{esc(offer.get("vendor", ""))}</span>'
            f'<span class="deadline {tone or "calm"}">{esc(when)}</span></div>'
            f"<h3>{esc(heading)}</h3>"
            + (f'<div class="models">{model_html}</div>' if model_html else "")
            + f"<p>{esc(offer.get('text', ''))}</p></div>"
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def render_bargains(bargains: list[dict]) -> str:
    """Models priced below the tier beneath them — the "why settle" list."""
    if not bargains:
        return '<p class="empty">Nothing is currently priced out of its class.</p>'

    cards = []
    for item in bargains:
        promo = item.get("promo")
        days = item.get("days_left")
        if promo and days is not None and days <= 7:
            tone = "urgent"
            left = "ends today" if days == 0 else f"{days} day{'s' if days != 1 else ''} left"
            caveat = f'<span class="deadline urgent">promo — {esc(left)}</span>'
        elif promo:
            tone = ""
            until = f" until {item['expires']}" if item.get("expires") else ""
            caveat = f'<span class="deadline">promo price{esc(until)}</span>'
        else:
            tone = "calm"
            caveat = '<span class="deadline calm">standard price</span>'

        pct = (1 - item["ratio"]) * 100
        cards.append(
            f'<div class="card {tone}" data-vendor="{esc(item["vendor"])}">'
            f'<div class="meta"><span class="chip">{esc(item["vendor"])}</span>'
            f'<span class="chip">{esc(item["tier"])}</span>{caveat}</div>'
            f'<h3>{esc(item["model"])}</h3>'
            f'<p class="why">A <strong>{esc(item["tier"].lower())}</strong> model at '
            f'<span class="price">${item["blended"]:.2f}</span> blended — '
            f'{pct:.0f}% below the typical <strong>{esc(item["compared_tier"].lower())}</strong> '
            f'model at <span class="price">${item["reference"]:.2f}</span>. '
            f'It undercuts {item["cheaper_than"]} of {item["of"]} of them outright.</p>'
            "</div>"
        )
    return f'<div class="cards two">{"".join(cards)}</div>'


def render_retiring(retiring: list[dict]) -> str:
    if not retiring:
        return ""
    items = []
    for row in retiring:
        days = row["days_left"]
        tone = "urgent" if days <= 14 else ""
        alt = row.get("alternative")
        items.append(
            f'<div class="card {tone}" data-vendor="Copilot">'
            f'<div class="meta"><span class="chip">Copilot</span>'
            f'<span class="deadline {tone or "calm"}">{days} day{"s" if days != 1 else ""} left</span></div>'
            f'<h3>{esc(row["model"])} retires {esc(row["date"])}</h3>'
            + (f"<p>Suggested replacement: <code>{esc(alt)}</code></p>" if alt else "")
            + "</div>"
        )
    return f'<div class="cards two">{"".join(items)}</div>'


def render_picks(picks: list[dict]) -> str:
    """Hand-written recommendations, rendered with live prices."""
    if not picks:
        return ""
    cards = []
    for pick in picks:
        stale = pick.get("stale")
        tone = "urgent" if stale else "calm"
        price = _price_pair(pick)

        if not pick.get("found"):
            flag = '<span class="stale">model no longer listed — re-check this pick</span>'
        elif stale:
            drift = pick.get("drift_percent") or 0.0
            direction = "up" if drift > 0 else "down"
            flag = (
                f'<span class="stale">price moved {abs(drift):.0f}% {direction} '
                f"since this was written — re-check</span>"
            )
        else:
            flag = ""

        cards.append(
            f'<div class="card {tone}">'
            f'<h3>{esc(pick.get("task", ""))}</h3>'
            + (f'<p>{esc(pick.get("detail", ""))}</p>' if pick.get("detail") else "")
            + f'<div class="models" style="margin-top:.45rem"><code>{esc(pick.get("model", ""))}</code>'
            + (f" &nbsp;{price}" if price else "")
            + "</div>"
            + f'<p class="why">{esc(pick.get("why", "").strip())}</p>'
            + (f"<p>{flag}</p>" if flag else "")
            + "</div>"
        )
    return f'<div class="cards two">{"".join(cards)}</div>'


def render_value(rows: list[dict]) -> str:
    """Cheapest live model per vendor and capability tier."""
    if not rows:
        return ""
    body = []
    for row in rows:
        cheapest = row["cheapest"]
        promo = (
            ' <span class="deadline urgent">promo price</span>'
            if cheapest.get("offer")
            else ""
        )
        runners = ", ".join(
            f'{esc(r["model"])} ${r["blended"]:.2f}' for r in row.get("runners_up", [])
        )
        body.append(
            "<tr>"
            f'<td>{esc(row["tier"])}</td>'
            f'<td>{esc(row["vendor"])}</td>'
            f'<td>{esc(cheapest["model"])}{promo}</td>'
            f'<td class="num">${cheapest["blended"]:.2f}</td>'
            f'<td class="num">{esc(diffing.fmt_value(cheapest.get("input"), "input"))} / '
            f'{esc(diffing.fmt_value(cheapest.get("output"), "output"))}</td>'
            f"<td>{runners or '—'}</td>"
            "</tr>"
        )
    return (
        '<div class="tablewrap"><table>'
        "<thead><tr><th>Tier</th><th>Vendor</th><th>Cheapest</th>"
        "<th>Blended</th><th>In / Out</th><th>Next cheapest</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def build_index(entries: list[dict], snapshots: list[dict], state: dict, overview: dict | None = None) -> str:
    price_entries = [e for e in entries if e.get("class") not in ADVISORY_CLASSES]
    advisories = [e for e in entries if e.get("class") in ADVISORY_CLASSES]

    checked = state.get("last_checked", "never")
    problems = state.get("problems") or []
    if problems:
        detail = "; ".join(f'{esc(p.get("source", "?"))}: {esc(p.get("message", ""))}' for p in problems)
        status = f'<span class="bad">Last run hit a problem</span> — {detail}'
    else:
        status = '<span class="ok">Last run was clean.</span>'

    tables = "".join(render_table(s) for s in snapshots)

    overview = overview or {}
    offers = overview.get("offers") or []
    retiring = overview.get("retiring") or []
    picks = overview.get("picks") or []
    value = overview.get("value") or []

    bargains = overview.get("bargains") or []
    bargains_block = (
        '<h2 id="bargains">Punching above its price</h2>'
        '<p class="note-inline">Models costing less than the typical model of the tier below them — '
        "the ones where you would otherwise settle for less and pay more. Promotional prices are "
        "marked, because an anomaly with an expiry date is a different proposition.</p>"
        f"{render_bargains(bargains)}"
    )
    retiring_block = (
        f'<h2 id="retiring">Retiring soon</h2>{render_retiring(retiring)}' if retiring else ""
    )
    picks_block = (
        "<h2>Good for this, right now</h2>"
        '<p class="note-inline">Hand-written, with prices looked up fresh on every build. '
        "A pick whose price has drifted is flagged rather than left to quietly mislead.</p>"
        f"{render_picks(picks)}"
        if picks
        else ""
    )
    value_block = (
        "<h2>Cheapest per tier</h2>"
        '<p class="note-inline">Blended cost weights input against output 3:1, the usual shape '
        "of chat and coding work. Retired and limited-availability models are excluded. "
        "Tiers are the vendor&rsquo;s own labels where they publish them.</p>"
        f"{render_value(value)}"
        if value
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(SITE_TITLE)}</title>
<meta name="description" content="{esc(SITE_TAGLINE)}">
<link rel="alternate" type="application/atom+xml" title="{esc(SITE_TITLE)}" href="feed.xml">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{esc(SITE_TITLE)}</h1>
  <p class="tagline">{esc(SITE_TAGLINE)}</p>
  <p class="status">Checked {esc(checked)}. {status} <a href="feed.xml">Atom feed</a></p>
  <nav class="jump">
    <a href="#bargains">Punching above its price</a>
    <a href="#offers">On offer</a>
    <a href="#picks">Good for this</a>
    <a href="#value">Cheapest per tier</a>
    <a href="#changed">What changed</a>
    <a href="#prices">All prices</a>
  </nav>
</header>

{bargains_block}

<h2 id="offers">On offer</h2>
<p class="note-inline">Promotions the vendors have written down, plus price cuts still in force.
Soonest deadline first.</p>
{render_offers(offers)}

{retiring_block}

<span id="picks"></span>
{picks_block}

<span id="value"></span>
{value_block}

<h2 id="changed">What changed</h2>
<div class="filters">
  <button data-filter="all" aria-pressed="true">All</button>
  <button data-filter="Anthropic" aria-pressed="false">Anthropic</button>
  <button data-filter="Copilot" aria-pressed="false">Copilot</button>
</div>
{render_changelog(price_entries)}

<h2>Worth reading</h2>
{render_changelog(advisories)}

<h2 id="prices">Current prices</h2>
{tables}

<footer>
  <p>Prices are per million tokens unless marked otherwise. Multipliers are premium-request
  multipliers on legacy annual Copilot plans, not money.</p>
  <p>Copilot history is backfilled from the
  <a href="https://github.com/github/docs">github/docs</a> repository, so it reaches back
  further than this tool has existed. Anthropic publishes no equivalent archive — its record
  starts when this tool first ran, and is not a complete history.</p>
  <p>Built by a scheduled job that reads each vendor's own published source. No estimates,
  no third-party aggregators. <a href="https://github.com/zrrbite/llm-price-watch">Source</a>.</p>
</footer>
</div>
<script>{FILTER_JS}</script>
</body>
</html>
"""


def build_feed(entries: list[dict], state: dict) -> str:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_date[entry.get("date", "unknown")].append(entry)

    updated = state.get("last_checked") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []
    for date in sorted(by_date, reverse=True)[:60]:
        day = by_date[date]
        lines = "".join(f"<li>{_fields_html(e) if e.get('class') not in ADVISORY_CLASSES else esc(e.get('text') or e.get('summary',''))}</li>" for e in day)
        content = esc(f"<ul>{lines}</ul>")
        noun = "change" if len(day) == 1 else "changes"
        items.append(
            f"""  <entry>
    <title>{esc(f"{date}: {len(day)} pricing {noun}")}</title>
    <link href="{esc(SITE_URL)}"/>
    <id>tag:zrrbite.github.io,2026:llm-price-watch/{esc(date)}</id>
    <updated>{esc(date)}T00:00:00Z</updated>
    <content type="html">{content}</content>
  </entry>"""
        )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{esc(SITE_TITLE)}</title>
  <subtitle>{esc(SITE_TAGLINE)}</subtitle>
  <link href="{esc(SITE_URL)}"/>
  <link rel="self" href="{esc(SITE_URL)}feed.xml"/>
  <id>tag:zrrbite.github.io,2026:llm-price-watch</id>
  <updated>{esc(updated)}</updated>
{chr(10).join(items)}
</feed>
"""


def write_site(
    root: Path, entries: list[dict], snapshots: list[dict], state: dict,
    overview: dict | None = None,
) -> None:
    site_dir = root / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(
        build_index(entries, snapshots, state, overview), encoding="utf-8"
    )
    (site_dir / "feed.xml").write_text(build_feed(entries, state), encoding="utf-8")
    # Pages would otherwise run the output through Jekyll.
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
