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


def build_index(entries: list[dict], snapshots: list[dict], state: dict) -> str:
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
</header>

<h2>What changed</h2>
<div class="filters">
  <button data-filter="all" aria-pressed="true">All</button>
  <button data-filter="Anthropic" aria-pressed="false">Anthropic</button>
  <button data-filter="Copilot" aria-pressed="false">Copilot</button>
</div>
{render_changelog(price_entries)}

<h2>Worth reading</h2>
{render_changelog(advisories)}

<h2>Current prices</h2>
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


def write_site(root: Path, entries: list[dict], snapshots: list[dict], state: dict) -> None:
    site_dir = root / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(build_index(entries, snapshots, state), encoding="utf-8")
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
