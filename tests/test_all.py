"""Tests for the parsers, the differ and the sanity gate.

Fixtures are the real vendor payloads as fetched on 2026-09-02, so the parser
tests fail if a vendor changes shape in a way the code does not handle — which
is the point. Run with:

    .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import changelog  # noqa: E402
import diffing  # noqa: E402
import notify  # noqa: E402
import render  # noqa: E402
import sources  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def rows_by_key(snapshot: dict) -> dict:
    return {r["key"]: r["values"] for r in snapshot["rows"]}


class TestHelpers(unittest.TestCase):
    def test_parse_money(self):
        self.assertEqual(sources.parse_money("$12.50 / MTok"), 12.5)
        self.assertEqual(sources.parse_money("$10 / MTok"), 10.0)
        self.assertEqual(sources.parse_money("$2.00"), 2.0)
        self.assertEqual(sources.parse_money("$1,250.00"), 1250.0)

    def test_parse_money_ignores_footnote_marker(self):
        # "$0.25 / MTok1" — the trailing 1 is a footnote, not part of the price.
        self.assertEqual(sources.parse_money("$0.25 / MTok1"), 0.25)

    def test_parse_money_keeps_non_numeric_text(self):
        # Kept verbatim so a later move to a real price registers as a change.
        self.assertEqual(sources.parse_money("Not applicable"), "Not applicable")
        self.assertIsNone(sources.parse_money(""))
        self.assertIsNone(sources.parse_money("—"))

    def test_clean_model_name_extracts_status(self):
        name, status = sources.clean_model_name(
            "Claude Opus 4 ([retired, except on Google Cloud](https://example.com))"
        )
        self.assertEqual(name, "Claude Opus 4")
        self.assertEqual(status, "retired, except on Google Cloud")

    def test_clean_model_name_plain(self):
        name, status = sources.clean_model_name("Claude Sonnet 5")
        self.assertEqual(name, "Claude Sonnet 5")
        self.assertIsNone(status)

    def test_markdown_table_pads_ragged_rows(self):
        text = "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 |\n"
        table = sources.parse_markdown_tables(text)[0]
        # A short row must not shift every later column.
        self.assertEqual(table[0], {"A": "1", "B": "2", "C": ""})

    def test_fmt_value_distinguishes_money_from_multiplier(self):
        self.assertEqual(diffing.fmt_value(57.0, "multiplier"), "57×")
        self.assertEqual(diffing.fmt_value(2.0, "input"), "$2")
        self.assertEqual(diffing.fmt_value(0.025, "cached_input"), "$0.025")
        self.assertEqual(diffing.fmt_value("GA", "release_status"), "GA")
        self.assertEqual(diffing.fmt_value(None, "input"), "—")


class TestAnthropicParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = sources.parse_anthropic(fixture("anthropic-pricing.md"))
        cls.rows = rows_by_key(cls.snapshot)

    def test_finds_models(self):
        self.assertGreaterEqual(len(self.snapshot["rows"]), 15)
        self.assertIn("Claude Sonnet 5", self.rows)
        self.assertIn("Claude Opus 5", self.rows)

    def test_prices_match_the_published_page(self):
        sonnet = self.rows["Claude Sonnet 5"]
        self.assertEqual(sonnet["input"], 2.0)
        self.assertEqual(sonnet["output"], 10.0)
        self.assertEqual(sonnet["cache_read"], 0.2)
        self.assertEqual(sonnet["cache_write_5m"], 2.5)
        self.assertEqual(sonnet["cache_write_1h"], 4.0)

    def test_batch_prices_merge_onto_the_same_row(self):
        # One row per model, not one per table.
        sonnet = self.rows["Claude Sonnet 5"]
        self.assertEqual(sonnet["batch_input"], 1.0)
        self.assertEqual(sonnet["batch_output"], 5.0)

    def test_retired_status_is_captured(self):
        self.assertEqual(
            self.rows["Claude Opus 4.1"]["status"],
            "retired, except on Bedrock and Google Cloud",
        )

    def test_fable_cheaper_cache_read_is_not_lost_to_the_footnote(self):
        self.assertEqual(self.rows["Claude Fable 5.1"]["cache_read"], 0.25)

    def test_extracts_the_sonnet_5_advisory(self):
        # The note that carried the real news on 2026-09-01. It is not in any
        # table, which is the whole reason advisories are parsed at all.
        keys = {a["key"] for a in self.snapshot["advisories"]}
        self.assertIn("claude-sonnet-5-introductory-pricing", keys)
        note = next(
            a for a in self.snapshot["advisories"]
            if a["key"] == "claude-sonnet-5-introductory-pricing"
        )
        self.assertIn("will not occur", note["text"])


class TestCopilotParsers(unittest.TestCase):
    def test_pricing(self):
        snapshot = sources.parse_copilot_pricing(fixture("copilot-models-and-pricing.yml"))
        rows = rows_by_key(snapshot)
        self.assertGreaterEqual(len(rows), 30)
        self.assertEqual(rows["Claude Haiku 4.5"]["input"], 1.0)
        self.assertEqual(rows["Claude Haiku 4.5"]["output"], 5.0)
        self.assertEqual(rows["Claude Haiku 4.5"]["provider"], "anthropic")

    def test_pricing_keys_are_unique_per_tier(self):
        # GPT-5.4 appears once per context-size tier; the name alone collides.
        snapshot = sources.parse_copilot_pricing(fixture("copilot-models-and-pricing.yml"))
        keys = [r["key"] for r in snapshot["rows"]]
        self.assertEqual(len(keys), len(set(keys)), "duplicate row keys would silently drop a tier")
        self.assertTrue(any("272K" in k for k in keys))

    def test_multipliers(self):
        snapshot = sources.parse_copilot_multipliers(fixture("copilot-multipliers.yml"))
        rows = rows_by_key(snapshot)
        self.assertEqual(rows["Claude Haiku 4.5"]["multiplier"], 0.33)
        self.assertEqual(rows["GPT-5.5"]["multiplier"], 57.0)
        self.assertEqual(rows["Claude Opus 4.8"]["multiplier"], 27.0)

    def test_deprecations(self):
        snapshot = sources.parse_copilot_deprecations(fixture("copilot-deprecations.yml"))
        rows = rows_by_key(snapshot)
        self.assertIn("MAI-Code-1-Flash", rows)
        self.assertEqual(rows["MAI-Code-1-Flash"]["retirement_date"], "2026-09-10")

    def test_malformed_yaml_raises(self):
        with self.assertRaises(ValueError):
            sources.parse_copilot_multipliers("not: a list\n")


def snap(source="anthropic", rows=None, advisories=None, floor=1):
    return {
        "source": source,
        "floor": floor,
        "rows": rows or [],
        "advisories": advisories or [],
    }


class TestDiffing(unittest.TestCase):
    def test_detects_a_price_change(self):
        old = snap(rows=[{"key": "Claude Sonnet 5", "values": {"input": 3.0, "output": 15.0}}])
        new = snap(rows=[{"key": "Claude Sonnet 5", "values": {"input": 2.0, "output": 10.0}}])
        changes = diffing.diff_snapshots(new, old)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["class"], diffing.PRICE_CHANGED)
        fields = {f["field"]: (f["old"], f["new"]) for f in changes[0]["fields"]}
        self.assertEqual(fields["input"], (3.0, 2.0))
        self.assertEqual(fields["output"], (15.0, 10.0))

    def test_detects_added_and_removed(self):
        old = snap(rows=[{"key": "Old", "values": {"input": 1.0}}])
        new = snap(rows=[{"key": "New", "values": {"input": 2.0}}])
        classes = {c["class"] for c in diffing.diff_snapshots(new, old)}
        self.assertEqual(classes, {diffing.MODEL_ADDED, diffing.MODEL_REMOVED})

    def test_identical_snapshots_produce_nothing(self):
        one = snap(rows=[{"key": "A", "values": {"input": 1.0, "status": "GA"}}])
        self.assertEqual(diffing.diff_snapshots(one, copy.deepcopy(one)), [])

    def test_first_run_produces_no_changes(self):
        # Otherwise day one buries the backfilled history under "everything added".
        new = snap(rows=[{"key": "A", "values": {"input": 1.0}}])
        self.assertEqual(diffing.diff_snapshots(new, None), [])

    def test_numeric_and_textual_changes_are_reported_separately(self):
        old = snap(rows=[{"key": "A", "values": {"input": 1.0, "release_status": "GA"}}])
        new = snap(rows=[{"key": "A", "values": {"input": 2.0, "release_status": "Preview"}}])
        classes = {c["class"] for c in diffing.diff_snapshots(new, old)}
        self.assertEqual(classes, {diffing.PRICE_CHANGED, diffing.DETAIL_CHANGED})

    def test_not_applicable_to_a_real_price_counts_as_a_price_change(self):
        old = snap(rows=[{"key": "A", "values": {"cache_write": "Not applicable"}}])
        new = snap(rows=[{"key": "A", "values": {"cache_write": 1.25}}])
        changes = diffing.diff_snapshots(new, old)
        self.assertEqual(changes[0]["class"], diffing.PRICE_CHANGED)

    def test_advisory_change_is_its_own_class(self):
        old = snap(advisories=[{"key": "n", "text": "increases on September 1"}])
        new = snap(advisories=[{"key": "n", "text": "the increase will not occur"}])
        changes = diffing.diff_snapshots(new, old)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["class"], diffing.ADVISORY_CHANGED)
        self.assertIn("will not occur", changes[0]["text"])

    def test_advisory_changes_never_masquerade_as_price_changes(self):
        old = snap(
            rows=[{"key": "A", "values": {"input": 1.0}}],
            advisories=[{"key": "n", "text": "before"}],
        )
        new = snap(
            rows=[{"key": "A", "values": {"input": 1.0}}],
            advisories=[{"key": "n", "text": "after"}],
        )
        classes = {c["class"] for c in diffing.diff_snapshots(new, old)}
        self.assertNotIn(diffing.PRICE_CHANGED, classes)


class TestSanityGate(unittest.TestCase):
    def test_zero_rows_is_refused(self):
        old = snap(rows=[{"key": str(i), "values": {}} for i in range(20)])
        with self.assertRaises(diffing.SanityError):
            diffing.check_sanity(snap(rows=[]), old)

    def test_losing_more_than_half_the_rows_is_refused(self):
        old = snap(rows=[{"key": str(i), "values": {}} for i in range(20)])
        new = snap(rows=[{"key": str(i), "values": {}} for i in range(9)])
        with self.assertRaises(diffing.SanityError) as ctx:
            diffing.check_sanity(new, old)
        self.assertIn("refusing", str(ctx.exception))

    def test_losing_a_few_rows_is_allowed_through_as_news(self):
        old = snap(rows=[{"key": str(i), "values": {}} for i in range(20)])
        new = snap(rows=[{"key": str(i), "values": {}} for i in range(18)])
        diffing.check_sanity(new, old)  # must not raise

    def test_first_run_below_the_floor_is_refused(self):
        # A half-broken parse must not establish a bad baseline.
        with self.assertRaises(diffing.SanityError):
            diffing.check_sanity(snap(rows=[{"key": "a", "values": {}}], floor=10), None)

    def test_first_run_above_the_floor_is_accepted(self):
        rows = [{"key": str(i), "values": {}} for i in range(12)]
        diffing.check_sanity(snap(rows=rows, floor=10), None)  # must not raise

    def test_real_snapshots_clear_their_own_floors(self):
        for name, parse in (
            ("anthropic-pricing.md", sources.parse_anthropic),
            ("copilot-models-and-pricing.yml", sources.parse_copilot_pricing),
            ("copilot-multipliers.yml", sources.parse_copilot_multipliers),
            ("copilot-deprecations.yml", sources.parse_copilot_deprecations),
        ):
            with self.subTest(name):
                diffing.check_sanity(parse(fixture(name)), None)


class TestChangelog(unittest.TestCase):
    def test_merge_is_idempotent(self):
        changes = [{"source": "anthropic", "class": diffing.PRICE_CHANGED,
                    "key": "A", "summary": "A: input $3 → $2", "fields": []}]
        entries = changelog.make_entries(changes, "2026-09-01")
        merged, added = changelog.merge([], entries)
        self.assertEqual(added, 1)
        merged, added = changelog.merge(merged, changelog.make_entries(changes, "2026-09-01"))
        self.assertEqual(added, 0, "re-running a day's job must not duplicate entries")
        self.assertEqual(len(merged), 1)

    def test_entry_id_separator_prevents_field_collisions(self):
        a = changelog.entry_id("d", "s", "c", "a b", "x")
        b = changelog.entry_id("d", "s", "c", "a", "b x")
        self.assertNotEqual(a, b)

    def test_sorted_newest_first(self):
        entries = [{"id": "1", "date": "2026-01-01"}, {"id": "2", "date": "2026-09-01"}]
        self.assertEqual(changelog.sort_entries(entries)[0]["date"], "2026-09-01")


class TestRenderAndNotify(unittest.TestCase):
    def setUp(self):
        self.entries = changelog.make_entries(
            [
                {"source": "anthropic", "class": diffing.PRICE_CHANGED, "key": "Claude Sonnet 5",
                 "summary": "x", "fields": [{"field": "input", "old": 3.0, "new": 2.0}]},
                {"source": "copilot-multipliers", "class": diffing.MODEL_ADDED,
                 "key": "GPT-5.5", "summary": "GPT-5.5 added", "fields": []},
            ],
            "2026-09-01",
        )
        self.snapshot = sources.parse_anthropic(fixture("anthropic-pricing.md"))
        self.state = {"last_checked": "2026-09-02T00:00:00Z", "problems": []}

    def test_index_contains_the_change_and_escapes_nothing_dangerous(self):
        page = render.build_index(self.entries, [self.snapshot], self.state)
        self.assertIn("Claude Sonnet 5", page)
        self.assertIn("$3", page)
        self.assertIn("$2", page)
        self.assertIn("Last run was clean", page)

    def test_index_surfaces_a_broken_parser(self):
        state = {"last_checked": "2026-09-02T00:00:00Z",
                 "problems": [{"source": "anthropic", "message": "parsed 0 rows"}]}
        page = render.build_index(self.entries, [self.snapshot], state)
        self.assertIn("Last run hit a problem", page)
        self.assertIn("parsed 0 rows", page)

    def test_feed_is_wellformed(self):
        import xml.etree.ElementTree as ET
        feed = render.build_feed(self.entries, self.state)
        root = ET.fromstring(feed)
        self.assertTrue(root.tag.endswith("feed"))
        self.assertEqual(len(root.findall("{http://www.w3.org/2005/Atom}entry")), 1)

    def test_issue_title_and_body(self):
        title = notify.issue_title(self.entries, [], "2026-09-01")
        self.assertIn("2 pricing changes", title)
        body = notify.issue_body(self.entries, [], "2026-09-01")
        self.assertIn("Price changes", body)
        self.assertIn("`$3`", body)

    def test_parser_problem_gets_its_own_title(self):
        problems = [{"source": "anthropic", "message": "parsed 0 rows"}]
        title = notify.issue_title([], problems, "2026-09-01")
        self.assertTrue(title.startswith("Parser broken:"))
        self.assertIn("nothing was committed", notify.issue_body([], problems, "2026-09-01"))

    def test_email_is_inert_without_configuration(self):
        import os
        for var in ("SMTP_HOST", "SMTP_TO"):
            os.environ.pop(var, None)
        self.assertFalse(notify.email_configured())
        self.assertFalse(notify.send_email("subject", "body"))


if __name__ == "__main__":
    unittest.main()
