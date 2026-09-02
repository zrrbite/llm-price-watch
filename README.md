# llm-price-watch

Tracks what **changed** in Anthropic and GitHub Copilot pricing, not just what
it costs today.

→ **[zrrbite.github.io/llm-price-watch](https://zrrbite.github.io/llm-price-watch/)**
· [Atom feed](https://zrrbite.github.io/llm-price-watch/feed.xml)

## Why

Vendors publish what a model costs right now. They do not publish a changelog.
The thing you actually miss is a *change* — a price cut, a promotional rate, a
scheduled increase that quietly got cancelled.

The case that prompted this: on 2026-09-01 Anthropic made Claude Sonnet 5's
`$2/$10` introductory pricing permanent, and the increase to `$3/$15` announced
for that date did not happen. That fact is not in the pricing table. It is one
sentence in a callout beside it. Read only the table and you learn nothing.

So this watches both the numbers **and** the prose around them.

## Sources

Every number comes from the vendor's own published source. No aggregators, no
estimates, and no model in the data path — the parsers are deterministic,
because an extractor that occasionally invents a price is worse than no tool.

| What | Source |
|---|---|
| Anthropic API pricing | [`pricing.md`](https://platform.claude.com/docs/en/about-claude/pricing) — the docs page served as raw markdown |
| Copilot token pricing | [`models-and-pricing.yml`](https://github.com/github/docs/blob/main/data/tables/copilot/models-and-pricing.yml) |
| Copilot legacy multipliers | [`annual-subscriber-model-multipliers.yml`](https://github.com/github/docs/blob/main/data/tables/copilot/annual-subscriber-model-multipliers.yml) |
| Copilot retirements | [`model-deprecation-history.yml`](https://github.com/github/docs/blob/main/data/tables/copilot/model-deprecation-history.yml) |

Copilot's published pages render those tables from Liquid templates and contain
no numbers themselves. The YAML data files behind them are the real source —
structured, and version-controlled in a public repo, which is also where the
backfilled history comes from.

## How it works

A scheduled Action fetches each source, parses it, compares it against the last
committed snapshot, and records what moved. Snapshots are committed, so the
repo's own git history is the price history.

Three decisions do the real work:

**Diff on values, not on text.** A textual diff fires on every wording tweak and
reflow. You would be ignoring the notifications within a month — at which point
the tool is worse than nothing, because you believe something is watching while
it isn't. Rows are compared on parsed numbers and set membership.

**Diff the prose notes too, separately.** This is what catches the Sonnet 5
case. Callouts are extracted and diffed as text, reported as *advisories*, and
kept apart from price deltas so the price feed stays trustworthy.

**A sanity gate.** When a vendor restructures a page, a naive parser returns
nothing and the differ faithfully reports every model as deleted. If a parse
yields under half the previous row count, the run refuses to commit and raises a
*parser broken* alert instead. That failure is guaranteed to happen eventually;
it must not look like news.

## Notifications

| Channel | Setup |
|---|---|
| Site | none |
| Atom feed | none |
| GitHub issue | none — uses the workflow token |
| Email | add the secrets below |

Email stays inert until configured: with no `SMTP_HOST` the step logs a line and
the run stays green. To switch it on, add repository secrets `SMTP_HOST`,
`SMTP_TO`, and as needed `SMTP_PORT` (default 587), `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM`.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install pyyaml

.venv/bin/python -m unittest discover -s tests   # 41 tests, no network
.venv/bin/python src/update.py --dry-run         # report, write nothing
.venv/bin/python src/update.py --cache           # reuse fetches while iterating
GITHUB_TOKEN=$(gh auth token) .venv/bin/python src/backfill.py
```

Fixtures under `tests/fixtures/` are the real vendor payloads as of
2026-09-02, so the parser tests fail if a vendor changes shape in a way the code
does not handle. That is intentional — it is the early warning.

Design notes: [`docs/specs/2026-09-02-llm-price-watch-design.md`](docs/specs/2026-09-02-llm-price-watch-design.md).

## Limits

- Anthropic publishes no historical archive, so its record starts when this tool
  first ran. Copilot's is backfilled from `github/docs` and reaches back to 2025.
- Two vendors only. OpenAI and Google were considered and cut: neither publishes
  a clean machine-readable source, and mixing verified numbers with
  aggregator-sourced ones under one roof would undermine both.
- List price is not job cost. A model at a fifth the price that needs three
  attempts and emits more output tokens is more expensive. This tracks prices,
  not value.
