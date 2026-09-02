"""Structured comparison of two snapshots, plus the sanity gate.

Two rules shape this module.

**Compare values, never text.** A textual diff of a vendor page fires on every
wording tweak, link change and reflow. Within a month you would be ignoring the
notifications -- at which point the tool is worse than nothing, because you
believe something is watching for you while it isn't.

**Never report mass deletion on a parse failure.** When a vendor restructures a
page, a naive parser returns nothing, the differ faithfully reports every model
as removed, and the site publishes a confidently-wrong empty table. The gate
below exists so that failure surfaces as "the parser is broken" instead.
"""

from __future__ import annotations

# Change classes, most to least consequential.
PRICE_CHANGED = "price_changed"
MODEL_ADDED = "model_added"
MODEL_REMOVED = "model_removed"
DETAIL_CHANGED = "detail_changed"
ADVISORY_ADDED = "advisory_added"
ADVISORY_CHANGED = "advisory_changed"
ADVISORY_REMOVED = "advisory_removed"

CLASS_ORDER = [
    PRICE_CHANGED,
    MODEL_ADDED,
    MODEL_REMOVED,
    ADVISORY_CHANGED,
    ADVISORY_ADDED,
    DETAIL_CHANGED,
    ADVISORY_REMOVED,
]

SANITY_RATIO = 0.5


class SanityError(RuntimeError):
    """A parse looks broken, so its result must not be trusted or committed."""

    def __init__(self, source: str, message: str):
        super().__init__(message)
        self.source = source
        self.message = message


def check_sanity(new: dict, old: dict | None) -> None:
    """Raise :class:`SanityError` when a parse is implausible.

    Against a previous snapshot the test is relative: losing more than half the
    rows is treated as breakage rather than as news. On a first run there is
    nothing to compare against, so each source's declared floor stands in --
    otherwise a half-broken parse could quietly establish a bad baseline, and
    every later comparison would be against nonsense.
    """
    source = new.get("source", "?")
    count = len(new.get("rows", []))

    if count == 0:
        raise SanityError(source, "parsed 0 rows; the page structure has probably changed")

    if old is None:
        floor = int(new.get("floor", 1))
        if count < floor:
            raise SanityError(
                source,
                f"first run parsed only {count} rows, below the plausibility floor of {floor}",
            )
        return

    previous = len(old.get("rows", []))
    if previous and count < previous * SANITY_RATIO:
        raise SanityError(
            source,
            f"parsed {count} rows, down from {previous} "
            f"(under {int(SANITY_RATIO * 100)}%); refusing to report this as removals",
        )


def _rows_by_key(snapshot: dict | None) -> dict[str, dict]:
    if not snapshot:
        return {}
    return {r["key"]: r.get("values", {}) for r in snapshot.get("rows", [])}


def _advisories_by_key(snapshot: dict | None) -> dict[str, str]:
    if not snapshot:
        return {}
    return {a["key"]: a.get("text", "") for a in snapshot.get("advisories", [])}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# Fields denominated in dollars per million tokens. Anything else numeric is
# a bare quantity -- a multiplier is not money, and rendering it as "$57" would
# be actively misleading.
MONEY_FIELDS = {
    "input",
    "output",
    "cached_input",
    "cache_read",
    "cache_write",
    "cache_write_5m",
    "cache_write_1h",
    "batch_input",
    "batch_output",
}


def _format_summary(key: str, fields: list[dict]) -> str:
    parts = [
        f"{f['field']} {fmt_value(f['old'], f['field'])} → {fmt_value(f['new'], f['field'])}"
        for f in fields
    ]
    return f"{key}: " + ", ".join(parts)


def fmt_value(value, field: str | None = None) -> str:
    if value is None:
        return "—"
    if _is_number(value):
        text = f"{float(value):.4f}".rstrip("0").rstrip(".")
        if field == "multiplier":
            return f"{text}×"
        if field in MONEY_FIELDS:
            return f"${text}"
        return text
    return str(value)


def diff_snapshots(new: dict, old: dict | None) -> list[dict]:
    """Return the changes between *old* and *new*, newest snapshot second.

    A first run (``old is None``) yields no changes. Announcing every model as
    newly added on day one would bury the real history that gets backfilled
    alongside it.
    """
    if old is None:
        return []

    source = new.get("source", "?")
    changes: list[dict] = []

    new_rows = _rows_by_key(new)
    old_rows = _rows_by_key(old)

    for key in sorted(new_rows.keys() - old_rows.keys()):
        changes.append(
            {
                "source": source,
                "class": MODEL_ADDED,
                "key": key,
                "fields": [],
                "summary": f"{key} added",
                "values": new_rows[key],
            }
        )

    for key in sorted(old_rows.keys() - new_rows.keys()):
        changes.append(
            {
                "source": source,
                "class": MODEL_REMOVED,
                "key": key,
                "fields": [],
                "summary": f"{key} removed",
                "values": old_rows[key],
            }
        )

    for key in sorted(new_rows.keys() & old_rows.keys()):
        new_values, old_values = new_rows[key], old_rows[key]
        numeric: list[dict] = []
        textual: list[dict] = []
        for field in sorted(set(new_values) | set(old_values)):
            before, after = old_values.get(field), new_values.get(field)
            if before == after:
                continue
            record = {"field": field, "old": before, "new": after}
            # A field is "money" if either side is a number, so a move from
            # "Not applicable" to a real price still counts as a price change.
            (numeric if _is_number(before) or _is_number(after) else textual).append(record)

        if numeric:
            changes.append(
                {
                    "source": source,
                    "class": PRICE_CHANGED,
                    "key": key,
                    "fields": numeric,
                    "summary": _format_summary(key, numeric),
                }
            )
        if textual:
            changes.append(
                {
                    "source": source,
                    "class": DETAIL_CHANGED,
                    "key": key,
                    "fields": textual,
                    "summary": _format_summary(key, textual),
                }
            )

    # Advisories are prose and are diffed as text -- deliberately kept apart
    # from price deltas. An advisory is a lower-confidence, higher-context
    # signal; folding it into the price feed would make the price feed
    # untrustworthy.
    new_notes = _advisories_by_key(new)
    old_notes = _advisories_by_key(old)

    for key in sorted(new_notes.keys() - old_notes.keys()):
        changes.append(
            {
                "source": source,
                "class": ADVISORY_ADDED,
                "key": key,
                "fields": [],
                "summary": new_notes[key],
                "text": new_notes[key],
            }
        )

    for key in sorted(new_notes.keys() & old_notes.keys()):
        if new_notes[key] != old_notes[key]:
            changes.append(
                {
                    "source": source,
                    "class": ADVISORY_CHANGED,
                    "key": key,
                    "fields": [],
                    "summary": new_notes[key],
                    "text": new_notes[key],
                    "previous_text": old_notes[key],
                }
            )

    for key in sorted(old_notes.keys() - new_notes.keys()):
        changes.append(
            {
                "source": source,
                "class": ADVISORY_REMOVED,
                "key": key,
                "fields": [],
                "summary": f"note withdrawn: {old_notes[key]}",
                "previous_text": old_notes[key],
            }
        )

    changes.sort(key=lambda c: (CLASS_ORDER.index(c["class"]), c["key"]))
    return changes
