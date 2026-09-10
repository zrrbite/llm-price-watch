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
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import changelog  # noqa: E402
import diffing  # noqa: E402
import insights  # noqa: E402
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


class TestFootnotes(unittest.TestCase):
    def test_strip_footnote_separates_name_from_marker(self):
        name, ref = sources.strip_footnote("GPT-5.6 Sol[^gpt-56-sol-promo]")
        self.assertEqual(name, "GPT-5.6 Sol")
        self.assertEqual(ref, "gpt-56-sol-promo")

    def test_plain_name_has_no_marker(self):
        self.assertEqual(sources.strip_footnote("GPT-5.6 Luna"), ("GPT-5.6 Luna", None))

    def test_marker_never_reaches_the_row_key(self):
        # It used to. GitHub adds the marker when a promo starts, which made
        # the same model read as removed-then-added instead of a price change.
        snapshot = sources.parse_copilot_pricing(fixture("copilot-models-and-pricing.yml"))
        self.assertFalse([r["key"] for r in snapshot["rows"] if "[^" in r["key"]])

    def test_promoted_models_are_flagged(self):
        snapshot = sources.parse_copilot_pricing(fixture("copilot-models-and-pricing.yml"))
        flagged = {r["key"] for r in snapshot["rows"] if r["values"].get("offer")}
        self.assertIn("GPT-5.6 Sol (≤ 272K)", flagged)
        self.assertIn("Gemini 3.7 Flash", flagged)

    def test_resolve_liquid_substitutes_variables(self):
        out = sources.resolve_liquid(
            "{% data variables.copilot.copilot_gpt_56_sol %} is 50% off",
            {"copilot_gpt_56_sol": "GPT-5.6 Sol"},
        )
        self.assertEqual(out, "GPT-5.6 Sol is 50% off")

    def test_parse_through_date(self):
        self.assertEqual(
            sources.parse_through_date("50% off through September 3, 2026."), "2026-09-03"
        )
        self.assertEqual(
            sources.parse_through_date("promotional pricing until December 31, 2026"), "2026-12-31"
        )
        self.assertIsNone(sources.parse_through_date("no date here"))

    def test_footnotes_parse_with_names_and_expiry(self):
        notes = sources.parse_copilot_footnotes(
            fixture("copilot-models-and-pricing-page.md"), fixture("copilot-variables.yml")
        )
        by_key = {n["key"]: n for n in notes}
        self.assertIn("gpt-56-sol-promo", by_key)
        self.assertEqual(by_key["gpt-56-sol-promo"]["expires"], "2026-09-03")
        self.assertTrue(by_key["gpt-56-sol-promo"]["text"].startswith("GPT-5.6 Sol"))
        self.assertNotIn("{%", by_key["gpt-56-sol-promo"]["text"])

    def test_pricing_survives_missing_extras(self):
        # The extras are optional context; losing them must not lose the prices.
        snapshot = sources.parse_copilot_pricing(fixture("copilot-models-and-pricing.yml"), {})
        self.assertGreater(len(snapshot["rows"]), 30)
        self.assertEqual(snapshot["advisories"], [])


def _snap(source, rows, advisories=None):
    return {"source": source, "url": "u", "rows": rows, "advisories": advisories or []}


class TestOffers(unittest.TestCase):
    def test_promotional_footnote_becomes_an_offer_with_a_deadline(self):
        snaps = [
            _snap(
                "copilot-pricing",
                [{"key": "GPT-5.6 Sol (≤ 272K)", "values": {"offer": "p", "input": 2.0, "output": 10.0}}],
                [{"key": "p", "text": "50% off", "expires": "2026-09-03"}],
            )
        ]
        offers = insights.find_offers(snaps, [], today=date(2026, 9, 2))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["days_left"], 1)
        self.assertEqual(offers[0]["models"], ["GPT-5.6 Sol"])

    def test_lapsed_offers_are_kept_but_marked(self):
        # Deliberately not dropped: see TestLapsedPromotions. A price sitting
        # past its promotion's end date is about to move, which is the most
        # actionable thing the tool can say.
        snaps = [
            _snap(
                "copilot-pricing",
                [{"key": "X", "values": {"offer": "p"}}],
                [{"key": "p", "text": "was 50% off", "expires": "2026-08-01"}],
            )
        ]
        offers = insights.find_offers(snaps, [], today=date(2026, 9, 2))
        self.assertEqual(len(offers), 1)
        self.assertTrue(offers[0]["lapsed"])

    def test_anthropic_boilerplate_is_not_an_offer(self):
        # Both of these matched on a looser pattern and were false positives.
        snaps = [
            _snap("anthropic", [], [
                {"key": "ccu", "text": "calculated at the applicable prices, after application of any discounts."},
                {"key": "bedrock", "text": "contact your representative to ensure your discounts are applied correctly."},
            ])
        ]
        self.assertEqual(insights.find_offers(snaps, [], today=date(2026, 9, 2)), [])

    def test_real_promotional_note_is_an_offer(self):
        snaps = [_snap("anthropic", [], [
            {"key": "s5", "text": "introductory pricing ... the scheduled increase will not occur."},
        ])]
        offers = insights.find_offers(snaps, [], today=date(2026, 9, 2))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["vendor"], "Anthropic")

    def test_offers_sort_soonest_deadline_first(self):
        snaps = [_snap("copilot-pricing",
                       [{"key": "A", "values": {"offer": "far"}}, {"key": "B", "values": {"offer": "near"}}],
                       [{"key": "far", "text": "x", "expires": "2026-12-31"},
                        {"key": "near", "text": "y", "expires": "2026-09-05"}])]
        offers = insights.find_offers(snaps, [], today=date(2026, 9, 2))
        self.assertEqual([o["days_left"] for o in offers], [3, 120])


class TestPriceCuts(unittest.TestCase):
    def _changelog(self, key, field, old, new, date_str="2026-08-01"):
        return [{
            "class": diffing.PRICE_CHANGED, "source": "copilot-pricing", "key": key,
            "date": date_str, "fields": [{"field": field, "old": old, "new": new}],
        }]

    def test_a_cut_still_in_force_is_reported(self):
        snaps = [_snap("copilot-pricing", [{"key": "M (≤ 200K)", "values": {"input": 0.2}}])]
        cuts = insights.recent_price_cuts(snaps, self._changelog("M (≤ 200K)", "input", 1.0, 0.2),
                                          today=date(2026, 9, 2))
        self.assertEqual(len(cuts), 1)
        self.assertAlmostEqual(cuts[0]["percent"], 80.0)

    def test_a_reversed_cut_is_not_an_offer(self):
        snaps = [_snap("copilot-pricing", [{"key": "M", "values": {"input": 1.0}}])]
        self.assertEqual(
            insights.recent_price_cuts(snaps, self._changelog("M", "input", 1.0, 0.2),
                                       today=date(2026, 9, 2)), []
        )

    def test_the_same_model_is_not_reported_once_per_context_tier(self):
        snaps = [_snap("copilot-pricing", [
            {"key": "M (≤ 200K)", "values": {"input": 0.2}},
            {"key": "M (> 200K)", "values": {"input": 0.4}},
        ])]
        entries = (self._changelog("M (≤ 200K)", "input", 1.0, 0.2)
                   + self._changelog("M (> 200K)", "input", 2.0, 0.4))
        cuts = insights.recent_price_cuts(snaps, entries, today=date(2026, 9, 2))
        self.assertEqual(len(cuts), 1, "one model, one offer")

    def test_cache_field_cuts_are_not_headline_offers(self):
        snaps = [_snap("copilot-pricing", [{"key": "M", "values": {"cached_input": 0.02}}])]
        self.assertEqual(
            insights.recent_price_cuts(snaps, self._changelog("M", "cached_input", 0.1, 0.02),
                                       today=date(2026, 9, 2)), []
        )

    def test_old_cuts_fall_out_of_the_window(self):
        snaps = [_snap("copilot-pricing", [{"key": "M", "values": {"input": 0.2}}])]
        self.assertEqual(
            insights.recent_price_cuts(snaps, self._changelog("M", "input", 1.0, 0.2, "2025-01-01"),
                                       today=date(2026, 9, 2)), []
        )


class TestValueTable(unittest.TestCase):
    def test_retired_and_limited_models_are_excluded(self):
        snaps = [_snap("anthropic", [
            {"key": "Claude Opus 4.1", "values": {"input": 1.0, "output": 1.0, "status": "retired, except on Bedrock"}},
            {"key": "Claude Mythos 5", "values": {"input": 0.5, "output": 0.5, "status": "limited availability"}},
            {"key": "Claude Opus 5", "values": {"input": 5.0, "output": 25.0}},
        ])]
        rows = insights.value_table(snaps)
        picked = {r["cheapest"]["model"] for r in rows}
        self.assertEqual(picked, {"Claude Opus 5"})

    def test_price_tie_prefers_the_newer_model(self):
        snaps = [_snap("anthropic", [
            {"key": "Claude Opus 4.5", "values": {"input": 5.0, "output": 25.0}},
            {"key": "Claude Opus 5", "values": {"input": 5.0, "output": 25.0}},
        ])]
        rows = insights.value_table(snaps)
        self.assertEqual(rows[0]["cheapest"]["model"], "Claude Opus 5")

    def test_blended_uses_the_stated_weighting(self):
        self.assertAlmostEqual(insights.blended({"input": 2.0, "output": 10.0}), 4.0)
        self.assertIsNone(insights.blended({"input": 2.0}))
        self.assertIsNone(insights.blended({"input": "Not applicable", "output": 1.0}))

    def test_real_snapshot_produces_sane_tiers(self):
        snapshot = sources.parse_anthropic(fixture("anthropic-pricing.md"))
        rows = insights.value_table([snapshot])
        tiers = {r["tier"]: r["cheapest"]["model"] for r in rows}
        self.assertEqual(tiers.get("Versatile"), "Claude Sonnet 5")
        self.assertEqual(tiers.get("Lightweight"), "Claude Haiku 4.5")
        self.assertNotIn("Mythos", tiers.get("Frontier", ""))


class TestBargains(unittest.TestCase):
    def _snaps(self):
        # Two Versatile models (median blended $5) and one Powerful at $4.
        return [_snap("copilot-pricing", [
            {"key": "Cheap V", "values": {"input": 2.0, "output": 10.0, "category": "Versatile"}},
            {"key": "Dear V", "values": {"input": 6.0, "output": 6.0, "category": "Versatile"}},
            {"key": "Sol (≤ 272K)", "values": {"input": 2.0, "output": 10.0, "category": "Powerful",
                                               "offer": "promo"}},
        ])]

    def test_finds_a_model_priced_below_the_tier_beneath_it(self):
        found = insights.find_bargains(self._snaps())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["model"], "Sol (≤ 272K)")
        self.assertEqual(found[0]["tier"], "Powerful")
        self.assertEqual(found[0]["compared_tier"], "Versatile")

    def test_reports_how_many_of_the_lower_tier_it_undercuts(self):
        found = insights.find_bargains(self._snaps())
        self.assertEqual(found[0]["cheaper_than"], 1)
        self.assertEqual(found[0]["of"], 2)

    def test_a_promotional_bargain_carries_its_deadline(self):
        offers = [{"models": ["Sol"], "expires": "2026-09-03", "days_left": 1}]
        found = insights.find_bargains(self._snaps(), offers)
        self.assertTrue(found[0]["promo"])
        self.assertEqual(found[0]["days_left"], 1)

    def test_a_dated_promotion_wins_over_an_undated_price_cut(self):
        # Both name the same model; the deadline is the part that matters.
        offers = [
            {"models": ["Sol"], "expires": None, "days_left": None},
            {"models": ["Sol"], "expires": "2026-09-03", "days_left": 1},
        ]
        found = insights.find_bargains(self._snaps(), offers)
        self.assertEqual(found[0]["days_left"], 1)

    def test_nothing_reported_when_prices_match_their_tier(self):
        snaps = [_snap("copilot-pricing", [
            {"key": "V", "values": {"input": 1.0, "output": 1.0, "category": "Versatile"}},
            {"key": "P", "values": {"input": 9.0, "output": 9.0, "category": "Powerful"}},
        ])]
        self.assertEqual(insights.find_bargains(snaps), [])

    def test_a_promotion_is_not_also_listed_as_a_price_cut(self):
        snaps = [_snap(
            "copilot-pricing",
            [{"key": "Sol (≤ 272K)", "values": {"offer": "p", "input": 2.0, "output": 10.0}}],
            [{"key": "p", "text": "50% off", "expires": "2026-09-03"}],
        )]
        entries = [{
            "class": diffing.PRICE_CHANGED, "source": "copilot-pricing",
            "key": "Sol (≤ 272K)", "date": "2026-08-20",
            "fields": [{"field": "input", "old": 4.0, "new": 2.0}],
        }]
        offers = insights.find_offers(snaps, entries, today=date(2026, 9, 2))
        self.assertEqual(len(offers), 1, "the cut and the promo are the same fact")
        self.assertEqual(offers[0]["kind"], "promotion")

    def test_real_data_flags_the_powerful_model_priced_like_a_versatile_one(self):
        snapshot = sources.parse_copilot_pricing(fixture("copilot-models-and-pricing.yml"))
        found = insights.find_bargains([snapshot])
        models = {b["model"].split(" (")[0] for b in found}
        self.assertIn("GPT-5.6 Sol", models)


class TestLapsedPromotions(unittest.TestCase):
    """A promo past its end date whose price has not reverted yet.

    This used to be the worst-handled state: the promotion was dropped as
    lapsed, and recent_price_cuts then re-reported the same model as a durable
    price cut — the exact opposite of the truth, in the section you act on.
    """

    def _snaps(self):
        return [_snap(
            "copilot-pricing",
            [{"key": "Sol (≤ 272K)", "values": {"offer": "p", "input": 2.0, "output": 10.0}}],
            [{"key": "p", "text": "50% off through September 3, 2026", "expires": "2026-09-03"}],
        )]

    def _cut(self):
        return [{
            "class": diffing.PRICE_CHANGED, "source": "copilot-pricing",
            "key": "Sol (≤ 272K)", "date": "2026-08-20",
            "fields": [{"field": "input", "old": 4.0, "new": 2.0}],
        }]

    def test_a_lapsed_promo_is_kept_and_labelled(self):
        offers = insights.find_offers(self._snaps(), [], today=date(2026, 9, 4))
        self.assertEqual(len(offers), 1)
        self.assertTrue(offers[0]["lapsed"])
        self.assertEqual(offers[0]["kind"], "lapsed promotion")
        self.assertIn("LAPSED", offers[0]["text"])
        self.assertIn("has not reverted", offers[0]["text"])

    def test_a_lapsed_promo_is_not_reclassified_as_a_durable_price_cut(self):
        offers = insights.find_offers(self._snaps(), self._cut(), today=date(2026, 9, 4))
        kinds = [o["kind"] for o in offers]
        self.assertEqual(kinds, ["lapsed promotion"])
        self.assertNotIn("price cut", kinds)

    def test_still_live_the_day_it_ends(self):
        offers = insights.find_offers(self._snaps(), [], today=date(2026, 9, 3))
        self.assertFalse(offers[0]["lapsed"])
        self.assertEqual(offers[0]["days_left"], 0)

    def test_lapsed_sorts_ahead_of_live_offers(self):
        snaps = [_snap(
            "copilot-pricing",
            [{"key": "A", "values": {"offer": "old"}}, {"key": "B", "values": {"offer": "new"}}],
            [{"key": "old", "text": "x", "expires": "2026-09-01"},
             {"key": "new", "text": "y", "expires": "2026-12-31"}],
        )]
        offers = insights.find_offers(snaps, [], today=date(2026, 9, 4))
        self.assertTrue(offers[0]["lapsed"], "the thing about to revert comes first")


class TestVendorIsolation(unittest.TestCase):
    def test_a_copilot_retirement_does_not_mark_the_anthropic_model_retired(self):
        # GitHub dropped Claude Opus 4.5 on 2026-09-01. Anthropic still sells it.
        snaps = [
            _snap("anthropic", [{"key": "Claude Opus 4.5", "values": {"input": 5.0, "output": 25.0}}]),
            _snap("copilot-pricing", [{"key": "Claude Opus 4.5", "values": {"input": 5.0, "output": 25.0}}]),
            _snap("copilot-deprecations", [{"key": "Claude Opus 4.5",
                                           "values": {"retirement_date": "2026-09-01"}}]),
        ]
        advice = insights.build_advice(snaps, [], today=date(2026, 9, 4))
        by_vendor = {(m["vendor"], m["name"]): m for m in advice["models"]}
        self.assertIsNone(by_vendor[("Anthropic", "Claude Opus 4.5")]["retires"])
        self.assertEqual(by_vendor[("Copilot", "Claude Opus 4.5")]["retires"], "2026-09-01")


class TestCheapEndpoints(unittest.TestCase):
    def _advice(self):
        snaps = [
            _snap("anthropic", [
                {"key": "Claude Sonnet 5", "values": {"input": 2.0, "output": 10.0}},
                {"key": "Claude Opus 4.1", "values": {"input": 15.0, "output": 75.0,
                                                      "status": "retired, except on Bedrock"}},
            ]),
            _snap("copilot-pricing",
                  [{"key": "Sol (≤ 272K)", "values": {"offer": "p", "input": 2.0, "output": 10.0,
                                                      "category": "Powerful"}}],
                  [{"key": "p", "text": "50% off", "expires": "2026-09-03"}]),
        ]
        return insights.build_advice(snaps, [], today=date(2026, 9, 4))

    def test_tsv_has_one_header_and_excludes_retired_models(self):
        tsv = render.build_tsv(self._advice())
        body = [l for l in tsv.splitlines() if l and not l.startswith("#")]
        self.assertTrue(body[0].startswith("model\tvendor\tclass"))
        self.assertNotIn("Claude Opus 4.1", tsv, "retired models are not choices")
        self.assertIn("Claude Sonnet 5", tsv)

    def test_tsv_marks_a_lapsed_promo_distinctly(self):
        tsv = render.build_tsv(self._advice())
        row = next(l for l in tsv.splitlines() if l.startswith("Sol"))
        self.assertIn("LAPSED", row)

    def test_tsv_is_far_smaller_than_the_json(self):
        import json as _json
        advice = self._advice()
        self.assertLess(len(render.build_tsv(advice)), len(_json.dumps(advice)) / 3)

    def test_brief_separates_live_offers_from_reverting_ones(self):
        brief = render.build_brief(self._advice())
        self.assertIn("PRICE ABOUT TO REVERT", brief)
        # It may legitimately still appear as cheapest in its class — that is
        # today's published price. What it must not do is sit under ON OFFER
        # as though it were still available.
        if "ON OFFER:" in brief:
            on_offer = brief.split("ON OFFER:")[1].split("\n\n")[0]
            self.assertNotIn("Sol", on_offer)
        self.assertIn("Sol", brief.split("PRICE ABOUT TO REVERT")[1])

    def test_brief_stays_small(self):
        self.assertLess(len(render.build_brief(self._advice())), 2000)


class TestPicks(unittest.TestCase):
    def test_live_price_is_attached(self):
        snaps = [_snap("anthropic", [{"key": "Claude Sonnet 5", "values": {"input": 2.0, "output": 10.0}}])]
        picks = insights.resolve_picks(
            [{"task": "t", "model": "Claude Sonnet 5", "source": "anthropic", "blended_when_written": 4.0}], snaps
        )
        self.assertTrue(picks[0]["found"])
        self.assertEqual(picks[0]["input"], 2.0)
        self.assertFalse(picks[0]["stale"])

    def test_a_moved_price_marks_the_pick_stale(self):
        snaps = [_snap("anthropic", [{"key": "M", "values": {"input": 4.0, "output": 20.0}}])]
        picks = insights.resolve_picks(
            [{"task": "t", "model": "M", "source": "anthropic", "blended_when_written": 4.0}], snaps
        )
        self.assertTrue(picks[0]["stale"])
        # blended went 4.0 -> 8.0, so exactly +100%.
        self.assertAlmostEqual(picks[0]["drift_percent"], 100.0)

    def test_a_vanished_model_marks_the_pick_stale(self):
        picks = insights.resolve_picks([{"task": "t", "model": "Gone", "source": "anthropic"}], [])
        self.assertFalse(picks[0]["found"])
        self.assertTrue(picks[0]["stale"])

    def test_shipped_picks_all_resolve(self):
        # The picks committed to the repo must name models that exist.
        import yaml as _yaml
        picks = _yaml.safe_load((ROOT / "data" / "picks.yml").read_text(encoding="utf-8"))
        snaps = [
            sources.parse_anthropic(fixture("anthropic-pricing.md")),
            sources.parse_copilot_pricing(fixture("copilot-models-and-pricing.yml")),
            sources.parse_copilot_multipliers(fixture("copilot-multipliers.yml")),
        ]
        for pick in insights.resolve_picks(picks, snaps):
            with self.subTest(pick["model"]):
                self.assertTrue(pick["found"], f"{pick['model']} is not in any snapshot")


class TestOverviewRendering(unittest.TestCase):
    def test_sections_render_and_stay_wellformed(self):
        snapshot = sources.parse_anthropic(fixture("anthropic-pricing.md"))
        overview = {
            "offers": [{"vendor": "Copilot", "kind": "promotion", "models": ["GPT-5.6 Sol"],
                        "text": "50% off", "expires": "2026-09-03", "days_left": 1}],
            "retiring": [{"model": "MAI-Code-1-Flash", "date": "2026-09-10",
                          "days_left": 8, "alternative": "MAI-Code-1.1-Flash"}],
            "value": insights.value_table([snapshot]),
            "picks": [{"task": "Bulk", "model": "Claude Haiku 4.5", "why": "cheap",
                       "found": True, "stale": False, "input": 1.0, "output": 5.0}],
        }
        page = render.build_index([], [snapshot], {"last_checked": "x", "problems": []}, overview)
        for needle in ("On offer", "1 day left", "Retiring soon", "MAI-Code-1-Flash",
                       "Good for this, right now", "Cheapest per tier"):
            self.assertIn(needle, page)

    def test_page_is_fine_without_any_overview(self):
        snapshot = sources.parse_anthropic(fixture("anthropic-pricing.md"))
        page = render.build_index([], [snapshot], {"last_checked": "x", "problems": []})
        self.assertIn("Nothing on offer", page)


def _load_check_script():
    """Import skill/llm-price-check/check.py, whose folder name is not a
    valid module name."""
    import importlib.util
    path = ROOT / "skill" / "llm-price-check" / "check.py"
    spec = importlib.util.spec_from_file_location("check_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSkillScript(unittest.TestCase):
    """The skill's own output wording. Two bugs have landed here — a negative
    'days left' and a future-tense retirement on a past date — both of which
    told the user the opposite of the truth while looking fine."""

    @classmethod
    def setUpClass(cls):
        cls.check = _load_check_script()

    def _verdict(self, **fields):
        return " | ".join(self.check.verdict(fields))

    def test_a_past_retirement_reads_as_past(self):
        out = self._verdict(retires="2020-05-01", replacement="Newer")
        self.assertIn("RETIRED 2020-05-01", out)
        self.assertNotIn("RETIRES", out)
        self.assertIn("Newer", out)

    def test_a_future_retirement_reads_as_future_with_a_countdown(self):
        future = (date.today() + timedelta(days=9)).isoformat()
        out = self._verdict(retires=future)
        self.assertIn("RETIRES in 9 day(s)", out)

    def test_a_retirement_today_says_today(self):
        out = self._verdict(retires=date.today().isoformat())
        self.assertIn("RETIRES TODAY", out)

    def test_an_unparseable_date_does_not_crash(self):
        self.assertIn("RETIRES soon-ish", self._verdict(retires="soon-ish"))

    def test_a_lapsed_promo_says_the_price_is_going_up(self):
        out = self._verdict(on_offer=True, offer_ends="2026-09-03", offer_days_left=-1)
        self.assertIn("PRICE ABOUT TO REVERT", out)
        self.assertNotIn("-1 day", out, "a negative countdown is never the right phrasing")

    def test_a_live_promo_counts_down(self):
        out = self._verdict(on_offer=True, offer_ends="2026-09-03", offer_days_left=3)
        self.assertIn("PROMO PRICE ENDING in 3 day(s)", out)

    def test_a_lapsed_alternative_is_not_sold_as_a_saving(self):
        out = self._verdict(cheaper_alternatives=[
            {"name": "X", "blended": 4.0, "saves_percent": 60.0,
             "on_offer": True, "offer_lapsed": True},
        ])
        self.assertIn("about to rise", out)

    def test_a_clean_model_produces_no_warnings(self):
        # The quiet case matters: a tool that comments on everything is ignored.
        self.assertEqual(self.check.verdict({"rank_in_tier": 1, "tier_size": 5}), [])

    def _overview(self, offers):
        return self.check.overview({
            "offers": offers, "retiring_soon": [],
            "cheapest_by_tier": {"X/Y": {"model": "M", "blended": 1.0}},
        })

    def test_overview_never_shows_a_negative_countdown(self):
        # The default invocation. It carried this bug after the per-model path
        # was fixed, because they are separate code paths.
        text = self._overview([
            {"models": ["Sol"], "days_left": -1, "expires": "2026-09-03",
             "lapsed": True, "text": "50% off", "vendor": "Copilot"},
        ])
        self.assertNotIn("-1d left", text)

    def test_overview_separates_reverting_from_available(self):
        text = self._overview([
            {"models": ["Sol"], "days_left": -1, "expires": "2026-09-03",
             "lapsed": True, "text": "over", "vendor": "Copilot"},
            {"models": ["Gemini"], "days_left": 118, "expires": "2026-12-31",
             "lapsed": False, "text": "live", "vendor": "Copilot"},
        ])
        self.assertIn("PRICE ABOUT TO REVERT", text)
        on_offer = text.split("ON OFFER NOW:")[1]
        self.assertIn("Gemini", on_offer)
        self.assertNotIn("Sol", on_offer)

    def test_overview_says_so_when_nothing_is_on_offer(self):
        text = self._overview([])
        self.assertIn("(nothing)", text)
        self.assertNotIn("PRICE ABOUT TO REVERT", text)

    def test_overview_infers_lapsed_from_a_negative_countdown_alone(self):
        # Older cached payloads predate the `lapsed` field.
        text = self._overview([
            {"models": ["Sol"], "days_left": -5, "expires": "2026-09-03",
             "text": "over", "vendor": "Copilot"},
        ])
        self.assertIn("PRICE ABOUT TO REVERT", text)


def _model(name, vendor="Copilot", tier="Versatile", blended=1.0, base=None, **extra):
    row = {"name": name, "base_name": base or name, "vendor": vendor, "tier": tier,
           "blended": blended, "input": blended, "output": blended, "available": True}
    row.update(extra)
    return row


# The catalogue that broke the old matcher: versions one dot apart, a family
# whose members have differently shaped names, context-window variants, and one
# model sold by two vendors.
MODELS = [
    _model("Claude Opus 4", "Anthropic", "Powerful", 30.0, available=False, status="retired"),
    _model("Claude Opus 4.5", "Anthropic", "Powerful", 10.0),
    _model("Claude Opus 4.8", "Anthropic", "Powerful", 10.0),
    _model("Claude Opus 5", "Anthropic", "Powerful", 10.0),
    _model("Claude Opus 5", "Copilot", "Powerful", 10.0),
    _model("Claude Haiku 4.5", "Anthropic", "Lightweight", 2.0),
    _model("Claude Haiku 4.5", "Copilot", "Versatile", 2.0),
    _model("GPT-5 mini", "Copilot", "Lightweight", 0.5),
    _model("GPT-5.4 (> 272K)", "Copilot", "Versatile", 9.0, base="GPT-5.4"),
    _model("GPT-5.4 (≤ 272K)", "Copilot", "Versatile", 5.0, base="GPT-5.4"),
    _model("GPT-5.4 mini", "Copilot", "Lightweight", 0.4),
    _model("Kimi K2.7 Code", "Copilot", "Versatile", 3.0),
    _model("Kimi K3", "Copilot", "Powerful", 6.0),
]


class TestSkillMatching(unittest.TestCase):
    """Targeting one model. The old matcher stripped punctuation and then
    substring-matched, so "opus 4" answered with every Opus from 4 to 4.8 and
    "gpt-5" with fourteen models — no way to ask about one model and get it."""

    @classmethod
    def setUpClass(cls):
        cls.check = _load_check_script()

    def _find(self, query, vendor=None):
        return sorted({m["name"] for m in self.check.find(MODELS, query, vendor)})

    def test_a_version_does_not_match_a_longer_version(self):
        # The whole bug: "4" is not "4.5", however the dot is normalised.
        self.assertEqual(self._find("opus 4"), ["Claude Opus 4"])

    def test_a_bare_major_version_does_not_drag_in_point_releases(self):
        self.assertEqual(self._find("gpt-5"), ["GPT-5 mini"])

    def test_separators_and_spacing_do_not_matter(self):
        for query in ("opus 5", "opus5", "claude-opus-5", "Claude Opus 5"):
            self.assertEqual(self._find(query), ["Claude Opus 5"], query)

    def test_a_model_sold_by_two_vendors_returns_both_rows(self):
        # Two real prices, not a duplicate. Collapsing them would hide one.
        self.assertEqual(len(self.check.find(MODELS, "opus 5")), 2)

    def test_a_vendor_filter_narrows_to_one_row(self):
        hits = self.check.find(MODELS, "opus 5", "Anthropic")
        self.assertEqual([m["vendor"] for m in hits], ["Anthropic"])

    def test_a_family_query_returns_the_whole_family(self):
        # Both Kimis, though their names are shaped differently. Ranking the
        # shorter name first here would silently drop the other one.
        self.assertEqual(self._find("kimi"), ["Kimi K2.7 Code", "Kimi K3"])

    def test_a_pinned_version_beats_a_longer_name_sharing_it(self):
        self.assertEqual(self._find("gpt 5.4"),
                         ["GPT-5.4 (> 272K)", "GPT-5.4 (≤ 272K)"])

    def test_the_longer_name_is_still_reachable_by_naming_it(self):
        self.assertEqual(self._find("gpt 5.4 mini"), ["GPT-5.4 mini"])

    def test_no_match_returns_nothing_rather_than_a_guess(self):
        self.assertEqual(self._find("llama"), [])
        self.assertEqual(self._find(""), [])


class TestSkillEncoding(unittest.TestCase):
    """Model names contain U+2264 and the output uses em dashes. On a Windows
    console, printing either raised UnicodeEncodeError and lost the whole
    answer — on the platform the skill tells people to run."""

    @classmethod
    def setUpClass(cls):
        cls.check = _load_check_script()

    def test_output_survives_a_legacy_console_codepage(self):
        import io
        stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        real = sys.stdout
        sys.stdout = stream
        try:
            self.check.use_utf8_output()
            self.assertEqual(sys.stdout.encoding.lower().replace("-", ""), "utf8")
            print(self.check.describe(_model("GPT-5.4 (≤ 272K)", blended=9.0)))
        finally:
            sys.stdout = real


class TestSkillRecommendation(unittest.TestCase):
    """Choosing a model from a description of the work."""

    @classmethod
    def setUpClass(cls):
        cls.check = _load_check_script()

    def test_light_work_lands_in_the_cheapest_tier(self):
        tier, matched = self.check.classify("just some light work")
        self.assertEqual(tier, "Lightweight")
        self.assertIn("light", matched)

    def test_hard_work_lands_in_a_capable_tier(self):
        self.assertEqual(self.check.classify("hard debugging")[0], "Powerful")

    def test_research_lands_at_the_frontier(self):
        self.assertEqual(self.check.classify("novel research")[0], "Frontier")

    def test_an_unrecognised_description_defaults_and_says_it_defaulted(self):
        # An empty match list is how the caller knows it was a default rather
        # than a reading of the request.
        self.assertEqual(self.check.classify("do the thing"), ("Versatile", []))

    def test_a_tie_goes_to_the_cheaper_tier(self):
        # "cheap" is Lightweight, "coding" is Versatile, one word each.
        # Overspending on work that did not need it is the costlier mistake.
        self.assertEqual(self.check.classify("cheap coding")[0], "Lightweight")

    def _rank(self, tier, vendor=None):
        groups = self.check._candidates(MODELS, tier, vendor)
        return {v: [m["name"] for m in rows] for v, rows in groups.items()}

    def test_a_retired_model_is_never_recommended(self):
        self.assertNotIn("Claude Opus 4", self._rank("Powerful")["Anthropic"])

    def test_candidates_are_ordered_by_price(self):
        self.assertEqual(self._rank("Versatile", "Copilot"),
                         {"Copilot": ["Claude Haiku 4.5", "Kimi K2.7 Code",
                                      "GPT-5.4 (≤ 272K)", "GPT-5.4 (> 272K)"]})

    def test_equal_prices_put_the_newer_version_first(self):
        # All three are $10. Heading the list with 4.5 because it comes first
        # in the feed is advice nobody wants.
        self.assertEqual(self._rank("Powerful", "Anthropic")["Anthropic"],
                         ["Claude Opus 5", "Claude Opus 4.8", "Claude Opus 4.5"])

    def test_vendors_are_not_merged_across_disagreeing_tiers(self):
        # Haiku 4.5 is Lightweight at Anthropic and Versatile on Copilot.
        self.assertEqual(self._rank("Lightweight"),
                         {"Anthropic": ["Claude Haiku 4.5"],
                          "Copilot": ["GPT-5.4 mini", "GPT-5 mini"]})

    def test_the_recommendation_names_the_next_tier_up(self):
        data = {"models": MODELS}
        text = self.check.recommend(data, "Lightweight")
        self.assertIn("Claude Haiku 4.5", text)
        self.assertIn("If Lightweight is not enough", text)
        self.assertIn("Versatile", text)

    def test_the_top_tier_offers_no_step_up(self):
        text = self.check.recommend({"models": MODELS}, "Frontier")
        self.assertNotIn("is not enough", text)
        self.assertIn("no available models", text)


if __name__ == "__main__":
    unittest.main()
