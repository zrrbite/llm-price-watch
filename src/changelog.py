"""The accumulated changelog: load, append, dedupe.

The changelog is the product. Everything else on the site is reference
material, so this file is deliberately dull and append-only: entries are
identified by a stable hash of their content, and re-running a day's job can
never produce duplicates or rewrite history.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = 1

# A separator that cannot occur inside any field, so ("a b", "c") and
# ("a", "b c") cannot hash to the same id.
_SEP = "\x00"


def entry_id(date: str, source: str, cls: str, key: str, summary: str) -> str:
    raw = _SEP.join([date, source, cls, key, summary])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("entries", [])
    return data if isinstance(data, list) else []


def save(path: Path, entries: list[dict]) -> None:
    ordered = sort_entries(entries)
    payload = {"schema": SCHEMA_VERSION, "count": len(ordered), "entries": ordered}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sort_entries(entries: list[dict]) -> list[dict]:
    """Newest first; stable within a date so output does not churn in git."""
    return sorted(entries, key=lambda e: (e.get("date", ""), e.get("id", "")), reverse=True)


def make_entries(changes: list[dict], date: str, ref: str | None = None) -> list[dict]:
    out = []
    for change in changes:
        summary = change.get("summary", "")
        source = change.get("source", "?")
        cls = change.get("class", "?")
        key = change.get("key", "")
        entry = {
            "id": entry_id(date, source, cls, key, summary),
            "date": date,
            "source": source,
            "class": cls,
            "key": key,
            "summary": summary,
        }
        if change.get("fields"):
            entry["fields"] = change["fields"]
        if change.get("text"):
            entry["text"] = change["text"]
        if change.get("previous_text"):
            entry["previous_text"] = change["previous_text"]
        if ref:
            entry["ref"] = ref
        if change.get("ref"):
            entry["ref"] = change["ref"]
        if change.get("note"):
            entry["note"] = change["note"]
        out.append(entry)
    return out


def merge(existing: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """Add *new* entries not already present. Returns (all entries, added)."""
    seen = {e.get("id") for e in existing}
    added = [e for e in new if e.get("id") not in seen]
    return sort_entries(existing + added), len(added)
