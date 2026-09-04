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
| Copilot promotions | [`models-and-pricing.md`](https://github.com/github/docs/blob/main/content/copilot/reference/copilot-billing/models-and-pricing.md) footnotes, plus [`variables/copilot.yml`](https://github.com/github/docs/blob/main/data/variables/copilot.yml) |

Promotions hide in a footnote marker on the model name — `GPT-5.6 Sol[^gpt-56-sol-promo]` —
whose text lives on the content page and whose model names come from a Liquid
variables file. That marker is both the only machine-readable "this is
temporary, and it ends on this date" signal either vendor publishes, and a trap:
it is part of the model name, so a key that keeps it reports the same model as
removed-then-added every time a promotion starts or ends.

Copilot's published pages render those tables from Liquid templates and contain
no numbers themselves. The YAML data files behind them are the real source —
structured, and version-controlled in a public repo, which is also where the
backfilled history comes from.

## Endpoints

Three sizes, because for an agent the cost of *reading* is part of the answer.
Measured on the live data:

| Endpoint | Size | ~Tokens | Use it when |
|---|---|---|---|
| [`brief.txt`](https://zrrbite.github.io/llm-price-watch/brief.txt) | 0.7 KB | ~180 | "what should I use, what should I avoid" |
| [`models.tsv`](https://zrrbite.github.io/llm-price-watch/models.tsv) | 3.3 KB | ~820 | comparing models on price against capability class |
| [`advice.json`](https://zrrbite.github.io/llm-price-watch/advice.json) | 48 KB | ~12,000 | everything: ranks, cheaper alternatives, offer text |

`models.tsv` is one line per usable model — no repeated keys, no prose, retired
models omitted — with a `class` column carrying the vendor's own capability tier
so price can be judged against what you get rather than in the abstract:

```
model	vendor	class	in	out	blended	offer	ends	retires
Claude Sonnet 5	Anthropic	Versatile	2	10	4	none	-	-
GPT-5.6 Sol (≤ 272K)	Copilot	Powerful	2	10	4	LAPSED	2026-09-03	-
```

If your consumer runs a script, size does not matter — only what it prints
reaches the context. Reach for the small endpoints when the file itself is
going into a prompt.

## What the page shows

Ordered so the actionable things come first and the raw data last.

**Punching above its price** — models costing less than the *typical* model of
the tier below them. This is the question a price table cannot answer: not what
is cheapest, but what is underpriced for what it is. Right now GPT-5.6 Sol is a
Powerful-tier model at $4.00 blended, 11% under the median Versatile model, and
it undercuts 9 of the 17 of them outright. Promotional prices are marked,
because an anomaly with an expiry date is a different proposition from a
permanent one.

**On offer** — promotions the vendors have written down, plus price cuts still
in force, soonest deadline first. Each cut is re-checked against the current
price, so a cut that was later reversed is not reported as a deal.

**Retiring soon** — models with a retirement date inside 45 days, with GitHub's
suggested replacement.

**Good for this, right now** — hand-written picks from `data/picks.yml`. This is
the one place opinion lives. Prices are not stored there; they are looked up on
every build, and a pick whose cost has drifted more than 10% since it was
written is flagged on the page rather than left to quietly mislead.

**Cheapest per tier**, then **What changed**, then the full tables.

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

.venv/bin/python -m unittest discover -s tests   # 86 tests, no network
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
