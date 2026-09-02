"""Fetch and parse vendor pricing sources into one common snapshot shape.

Every source normalises to::

    {
      "source": "anthropic",
      "title":  "Anthropic API pricing",
      "url":    "https://...",
      "fetched": "2026-09-02T10:14:00Z",
      "floor":  10,                       # plausibility floor for a first run
      "rows":   [{"key": "Claude Sonnet 5", "values": {"input": 2.0, ...}}],
      "advisories": [{"key": "...", "text": "..."}],
    }

Row values may be floats or strings. The differ classifies a change by the type
it finds, so a price move and a status change are reported differently without
either source needing to declare which of its fields are money.

Parsers here are deliberately deterministic. A probabilistic extractor that
occasionally invents a price would be worse than having no tool at all.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

USER_AGENT = "llm-price-watch (+https://github.com/zrrbite/llm-price-watch)"
TIMEOUT = 30

GITHUB_DOCS_RAW = "https://raw.githubusercontent.com/github/docs/main/"
GITHUB_DOCS_BLOB = "https://github.com/github/docs/blob/main/"


# --------------------------------------------------------------------------
# fetching


class FetchError(RuntimeError):
    """A source could not be retrieved."""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str, cache_dir: Path | None = None) -> str:
    """GET *url* as text.

    With *cache_dir*, a previously fetched copy is reused. That is for local
    development only — the scheduled run always goes to the network.
    """
    cache_file = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".txt")
        if cache_file.exists():
            return cache_file.read_text(encoding="utf-8")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        # urlopen raises HTTPError (a URLError) for any non-2xx, so a status
        # check here would be dead code.
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise FetchError(f"{url} could not be fetched: {exc}") from exc

    if not text.strip():
        raise FetchError(f"{url} returned an empty body")
    if cache_file is not None:
        cache_file.write_text(text, encoding="utf-8")
    return text


# --------------------------------------------------------------------------
# small parsing helpers

_MONEY = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
# A markdown link inside parentheses, e.g. "Claude Opus 4 ([retired, ...](url))"
_PAREN_LINK = re.compile(r"\s*\(\[([^\]]+)\]\([^)]*\)\)")
_BARE_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def parse_money(cell: str) -> float | str | None:
    """Return the number in a price cell, or the cell text when it holds none.

    ``"$12.50 / MTok"`` -> ``12.5``; ``"$0.25 / MTok1"`` -> ``0.25`` (the
    trailing 1 is a footnote marker); ``"Not applicable"`` -> that string, so a
    later change to a real price still registers.
    """
    if cell is None:
        return None
    cell = cell.strip()
    if not cell or cell in {"-", "—", "N/A"}:
        return None
    match = _MONEY.search(cell)
    if match:
        return float(match.group(1).replace(",", ""))
    return cell


def clean_model_name(cell: str) -> tuple[str, str | None]:
    """Split a model cell into its name and any parenthesised status link.

    ``"Claude Opus 4 ([retired, except on Google Cloud](url))"``
    -> ``("Claude Opus 4", "retired, except on Google Cloud")``

    The status is kept as a diffable value: a model becoming retired is news.
    """
    status = None
    match = _PAREN_LINK.search(cell)
    if match:
        status = " ".join(match.group(1).split())
        cell = _PAREN_LINK.sub("", cell)
    cell = _BARE_LINK.sub(r"\1", cell)
    return " ".join(cell.split()), status


def split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c)


def parse_markdown_tables(text: str) -> list[list[dict[str, str]]]:
    """Return every GFM table in *text* as a list of header-keyed row dicts."""
    tables: list[list[dict[str, str]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines):
            header = split_table_row(lines[i])
            if is_separator_row(split_table_row(lines[i + 1])):
                rows: list[dict[str, str]] = []
                j = i + 2
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    cells = split_table_row(lines[j])
                    if not is_separator_row(cells):
                        # Pad or trim so a ragged row cannot shift every column.
                        cells = (cells + [""] * len(header))[: len(header)]
                        rows.append(dict(zip(header, cells)))
                    j += 1
                if rows:
                    tables.append(rows)
                i = j
                continue
        i += 1
    return tables


def find_column(headers, *needles: str) -> str | None:
    """First header containing all of *needles*, case-insensitively."""
    for header in headers:
        low = header.lower()
        if all(n in low for n in needles):
            return header
    return None


def slug(text: str, words: int = 6) -> str:
    parts = re.sub(r"[^a-z0-9\s]", "", text.lower()).split()
    return "-".join(parts[:words]) or hashlib.sha256(text.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# Anthropic

ANTHROPIC_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"
ANTHROPIC_HUMAN_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

_NOTE = re.compile(r"<Note(?:\s+id=\"([^\"]+)\")?\s*>(.*?)</Note>", re.S)


def parse_anthropic(text: str) -> dict:
    """Parse the Anthropic pricing page.

    Standard and batch prices are merged onto one row per model, so the site
    shows a single line per model rather than the same name twice.
    """
    rows: dict[str, dict] = {}

    for table in parse_markdown_tables(text):
        headers = list(table[0].keys())
        model_col = find_column(headers, "model")
        if not model_col:
            continue

        mapping: dict[str, str] = {}
        for field, needles in (
            ("input", ("base input",)),
            ("cache_write_5m", ("5m cache",)),
            ("cache_write_1h", ("1h cache",)),
            ("cache_read", ("cache hits",)),
            ("output", ("output token",)),
            ("batch_input", ("batch input",)),
            ("batch_output", ("batch output",)),
        ):
            col = find_column(headers, *needles)
            if col:
                mapping[field] = col
        if not mapping:
            continue  # not a pricing table (tool-token counts, runtime, etc.)

        for raw in table:
            name, status = clean_model_name(raw[model_col])
            if not name or name.lower() == "model":
                continue
            row = rows.setdefault(name, {"key": name, "values": {}})
            if status:
                row["values"]["status"] = status
            for field, col in mapping.items():
                value = parse_money(raw[col])
                if value is not None:
                    row["values"][field] = value

    advisories = []
    seen: set[str] = set()
    for note_id, body in _NOTE.findall(text):
        clean = _BARE_LINK.sub(r"\1", body)
        clean = " ".join(clean.split())
        if not clean:
            continue
        key = note_id or slug(clean)
        if key in seen:
            continue
        seen.add(key)
        advisories.append({"key": key, "text": clean})

    return {
        "source": "anthropic",
        "title": "Anthropic API pricing",
        "url": ANTHROPIC_HUMAN_URL,
        "fetched": utcnow(),
        "floor": 10,
        "rows": sorted(rows.values(), key=lambda r: r["key"]),
        "advisories": advisories,
    }


# --------------------------------------------------------------------------
# GitHub Copilot
#
# The published Copilot pages render these tables from Liquid templates and
# contain no numbers themselves. The numbers live in YAML data files in the
# public github/docs repository, which is both structured and version
# controlled -- see docs/specs for why that is the better source.

COPILOT_PRICING_PATH = "data/tables/copilot/models-and-pricing.yml"
COPILOT_MULTIPLIER_PATH = "data/tables/copilot/annual-subscriber-model-multipliers.yml"
COPILOT_DEPRECATION_PATH = "data/tables/copilot/model-deprecation-history.yml"
# The promotional footnotes referenced from model names live on the content
# page, not in the data file, and the variables they interpolate live in a
# third. Together they are the only machine-readable statement of what is
# currently discounted and until when.
COPILOT_NOTES_PATH = "content/copilot/reference/copilot-billing/models-and-pricing.md"
COPILOT_VARS_PATH = "data/variables/copilot.yml"

_NOT_APPLICABLE = {"not applicable", "n/a", "none", "-"}

# "GPT-5.6 Sol[^gpt-56-sol-promo]" -> name plus the footnote it points at.
_FOOTNOTE_REF = re.compile(r"\[\^([a-z0-9-]+)\]")
# "[^ref]: text" at the start of a line, continuing until a blank line.
_FOOTNOTE_DEF = re.compile(r"^\[\^([a-z0-9-]+)\]:[ \t]*(.+)$", re.M)
_LIQUID_VAR = re.compile(r"\{%\s*data\s+variables\.copilot\.([a-z0-9_]+)\s*%\}")
_LIQUID_ANY = re.compile(r"\{%.*?%\}")
# "through September 3, 2026" / "until December 31, 2026"
_THROUGH_DATE = re.compile(
    r"\b(?:through|until|ends?(?:\s+on)?)\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(\d{4})",
    re.I,
)

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}


def strip_footnote(name: str) -> tuple[str, str | None]:
    """Split ``"GPT-5.6 Sol[^gpt-56-sol-promo]"`` into name and footnote id.

    The marker must not stay in the key. GitHub adds and removes these when a
    promotion starts or ends, and a key that changes with it would report the
    same model as removed-then-added instead of as a price change.
    """
    ref = None
    match = _FOOTNOTE_REF.search(name)
    if match:
        ref = match.group(1)
        name = _FOOTNOTE_REF.sub("", name)
    return name.strip(), ref


def resolve_liquid(text: str, variables: dict[str, str]) -> str:
    """Replace ``{% data variables.copilot.x %}`` with its value."""
    text = _LIQUID_VAR.sub(lambda m: variables.get(m.group(1), ""), text)
    return " ".join(_LIQUID_ANY.sub("", text).split())


def parse_through_date(text: str) -> str | None:
    """ISO date an offer runs to, if the text states one."""
    match = _THROUGH_DATE.search(text)
    if not match:
        return None
    month = _MONTHS[match.group(1).lower()]
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


def parse_copilot_footnotes(notes_md: str, vars_yml: str) -> list[dict]:
    """Promotional footnotes from the Copilot pricing page.

    These are the only place GitHub states, in machine-readable form, that a
    price is temporary and when it stops being temporary.
    """
    try:
        variables = yaml.safe_load(vars_yml) or {}
    except Exception:  # noqa: BLE001
        variables = {}
    if not isinstance(variables, dict):
        variables = {}

    out = []
    for ref, body in _FOOTNOTE_DEF.findall(notes_md):
        text = resolve_liquid(body, variables)
        if not text:
            continue
        entry = {"key": ref, "text": text}
        expires = parse_through_date(text)
        if expires:
            entry["expires"] = expires
        out.append(entry)
    return out


def _yaml_entries(text: str) -> list[dict]:
    data = yaml.safe_load(text)
    if not isinstance(data, list):
        raise ValueError("expected a YAML list of mappings")
    return [d for d in data if isinstance(d, dict)]


def _copilot_value(raw) -> float | str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.lower() in _NOT_APPLICABLE:
        return text
    return parse_money(text)


def parse_copilot_pricing(text: str, extras: dict[str, str] | None = None) -> dict:
    """Current usage-based per-token pricing for models offered in Copilot.

    *extras* may carry ``notes`` (the content page) and ``vars`` (the Liquid
    variables), which together supply the promotional footnotes.
    """
    rows = []
    for entry in _yaml_entries(text):
        model = str(entry.get("model", "")).strip()
        if not model:
            continue
        model, footnote = strip_footnote(model)
        threshold = str(entry.get("threshold", "") or "").strip()
        # GPT-5.4 and friends appear once per context-size tier, so the model
        # name alone is not unique.
        key = model if threshold.lower() in _NOT_APPLICABLE or not threshold else f"{model} ({threshold})"

        values: dict[str, float | str] = {}
        for field in ("input", "cached_input", "output", "cache_write"):
            value = _copilot_value(entry.get(field))
            if value is not None:
                values[field] = value
        for field in ("provider", "release_status", "category", "notes"):
            value = entry.get(field)
            if value:
                values[field] = str(value).strip()
        # Diffable, so a promotion starting or ending is itself reported.
        if footnote:
            values["offer"] = footnote
        if values:
            rows.append({"key": key, "values": values})

    extras = extras or {}
    advisories = []
    if extras.get("notes"):
        advisories = parse_copilot_footnotes(extras["notes"], extras.get("vars", ""))

    return {
        "source": "copilot-pricing",
        "title": "GitHub Copilot — usage-based token pricing",
        "url": GITHUB_DOCS_BLOB + COPILOT_PRICING_PATH,
        "fetched": utcnow(),
        "floor": 15,
        "rows": sorted(rows, key=lambda r: r["key"]),
        "advisories": advisories,
    }


def parse_copilot_multipliers(text: str) -> dict:
    """Legacy premium-request multipliers, still live for legacy annual plans."""
    rows = []
    for entry in _yaml_entries(text):
        model = str(entry.get("model", "")).strip()
        if not model:
            continue
        raw = entry.get("new_multiplier", entry.get("multiplier"))
        if raw is None:
            continue
        try:
            value: float | str = float(str(raw).strip().rstrip("x"))
        except ValueError:
            value = str(raw).strip()
        rows.append({"key": model, "values": {"multiplier": value}})

    return {
        "source": "copilot-multipliers",
        "title": "GitHub Copilot — legacy premium-request multipliers",
        "url": GITHUB_DOCS_BLOB + COPILOT_MULTIPLIER_PATH,
        "fetched": utcnow(),
        "floor": 10,
        "rows": sorted(rows, key=lambda r: r["key"]),
        "advisories": [],
    }


def parse_copilot_deprecations(text: str) -> dict:
    """Retirement dates. A newly scheduled retirement is worth knowing early."""
    rows = []
    for entry in _yaml_entries(text):
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        values = {}
        for field in ("retirement_date", "suggested_alternative"):
            value = entry.get(field)
            if value:
                values[field] = str(value).strip()
        if values:
            rows.append({"key": name, "values": values})

    return {
        "source": "copilot-deprecations",
        "title": "GitHub Copilot — model retirements",
        "url": GITHUB_DOCS_BLOB + COPILOT_DEPRECATION_PATH,
        "fetched": utcnow(),
        "floor": 3,
        "rows": sorted(rows, key=lambda r: r["key"]),
        "advisories": [],
    }


# --------------------------------------------------------------------------
# registry

SOURCES = {
    "anthropic": {
        "url": ANTHROPIC_URL,
        "parse": parse_anthropic,
        "vendor": "Anthropic",
    },
    "copilot-pricing": {
        "url": GITHUB_DOCS_RAW + COPILOT_PRICING_PATH,
        "parse": parse_copilot_pricing,
        "vendor": "Copilot",
        "docs_path": COPILOT_PRICING_PATH,
        "extras": {
            "notes": GITHUB_DOCS_RAW + COPILOT_NOTES_PATH,
            "vars": GITHUB_DOCS_RAW + COPILOT_VARS_PATH,
        },
    },
    "copilot-multipliers": {
        "url": GITHUB_DOCS_RAW + COPILOT_MULTIPLIER_PATH,
        "parse": parse_copilot_multipliers,
        "vendor": "Copilot",
        "docs_path": COPILOT_MULTIPLIER_PATH,
    },
    "copilot-deprecations": {
        "url": GITHUB_DOCS_RAW + COPILOT_DEPRECATION_PATH,
        "parse": parse_copilot_deprecations,
        "vendor": "Copilot",
        "docs_path": COPILOT_DEPRECATION_PATH,
    },
}


def collect(source_id: str, cache_dir: Path | None = None) -> dict:
    spec = SOURCES[source_id]
    text = fetch(spec["url"], cache_dir=cache_dir)
    extras_spec = spec.get("extras")
    if not extras_spec:
        return spec["parse"](text)

    extras = {}
    for name, url in extras_spec.items():
        try:
            extras[name] = fetch(url, cache_dir=cache_dir)
        except FetchError as exc:
            # A missing extra costs context, not correctness: the price rows
            # still parse. Losing the whole source over it would be worse.
            print(f"{source_id}: optional extra '{name}' unavailable ({exc})")
    return spec["parse"](text, extras)
