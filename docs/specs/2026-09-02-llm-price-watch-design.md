# llm-price-watch — design

**Date:** 2026-09-02
**Status:** approved, building

## The problem

Vendor pricing pages tell you what a model costs *today*. They do not tell you
what changed. The thing that actually gets missed is a change — a price cut, a
promotional rate, a scheduled increase that got cancelled — and by the time you
notice, you have been paying the wrong amount, or budgeting against a number
that moved months ago.

Worked example, and the reason this exists. On 2026-09-01 Anthropic made Claude
Sonnet 5's `$2/$10` introductory pricing permanent; the increase to `$3/$15`
that had been announced for that date did not happen. That fact is not in the
pricing table. It is one sentence in a `<Note>` callout beside it. Anyone
reading only the table sees `$2 / $10` and learns nothing.

**So the product is the changelog. The price table is supporting material.**

## Scope

Anthropic and GitHub Copilot only. Not OpenAI or Google — both were considered
and cut, because neither publishes a clean machine-readable source and pulling
them in via a third-party aggregator would mean the site mixes verified numbers
with unverified ones under one roof. Better to cover two vendors honestly.

## Sources

All four were verified by fetching them on 2026-09-02.

| Vendor | URL | Format | Notes |
|---|---|---|---|
| Anthropic | `platform.claude.com/docs/en/about-claude/pricing.md` | Markdown | Official docs page served as raw markdown, 45 KB, HTTP 200. Frontmatter + GFM tables + `<Note>` blocks. |
| Copilot | `github/docs` → `data/tables/copilot/models-and-pricing.yml` | YAML | Current usage-based per-token pricing, 429 lines. The one that matters now. |
| Copilot | `github/docs` → `data/tables/copilot/annual-subscriber-model-multipliers.yml` | YAML | Legacy premium-request multipliers, still live for legacy annual Pro/Pro+. |
| Copilot | `github/docs` → `data/tables/copilot/model-deprecation-history.yml` | YAML | Dated deprecations. Changelog material on its own. |

### Correction to the original sketch

The first design said "parse the Copilot multiplier table out of the rendered
markdown." That is wrong and would not have worked. The published page at
`model-multipliers-for-annual-plans.md` contains no numbers — the table is a
Liquid template:

```liquid
| {% for entry in tables.copilot.annual-subscriber-model-multipliers %} |
| {{ entry.model }} | {{ entry.new_multiplier }} |
| {% endfor %} |
```

The numbers live in `data/tables/copilot/*.yml`. This is better than the
original plan, not worse: the real source is structured YAML with a documented
column format, so the Copilot side needs no text parsing at all. Only Anthropic
requires markdown table parsing.

### Why `github/docs` is the good source

It is a public git repository, so the change history already exists and is
annotated by GitHub themselves:

```
2026-07-07  add GitHub Copilot app support for auto model selection (#62075)
2026-06-08  new models not available for legacy annual billing (#61614)
2026-05-29  Feature branch for the usage-based billing GA (May 29) (#61278)
```

We do not build Copilot history. We read it. See *Backfill*.

## Architecture

Static site, no server, no API keys for the data path, no model in the loop.
A scheduled GitHub Action does everything and commits its results, so the repo's
own git history becomes the price history.

```
daily cron
    │
    ├─ fetch  ── anthropic.md, three copilot .yml files
    ├─ parse  ── normalise to a common snapshot shape
    ├─ gate   ── sanity check vs previous snapshot   ──┐ fails → parser-broken alert,
    │                                                  │         nothing committed
    ├─ diff   ── structured comparison ────────────────┘
    ├─ if changed:
    │     ├─ append dated entries to data/changelog.json
    │     ├─ commit new snapshots  (git = price history)
    │     ├─ rebuild site/index.html + site/feed.xml
    │     ├─ open a GitHub issue with the diff
    │     └─ send email (if SMTP secrets present)
    └─ always: stamp last-checked into the site footer
```

### Snapshot shape

Every source normalises to the same structure, so the differ is written once:

```json
{
  "source": "anthropic",
  "fetched": "2026-09-02T10:14:00Z",
  "rows": [
    {"key": "Claude Sonnet 5", "input": 2.0, "output": 10.0,
     "cache_read": 0.2, "cache_write_5m": 2.5, "cache_write_1h": 4.0}
  ],
  "advisories": [
    {"key": "sonnet-5-introductory-pricing", "text": "The $2/$10 ... is now the standard price."}
  ]
}
```

`rows` carry numbers and are diffed numerically. `advisories` carry prose and
are diffed as text. They are reported differently and must never be conflated —
see below.

## The three decisions that make this work

### 1. Diff on values, not on text

A text diff of the Anthropic page fires on every wording tweak, link change and
reflow. Within a month you would be ignoring the notifications, at which point
the whole thing is worse than useless — it is a thing you believe is watching
for you while it isn't.

So rows are compared on parsed numeric values and on set membership. The change
classes are:

- `price_changed` — a numeric field moved. Reports old → new, per field.
- `model_added` — a key present now, absent before.
- `model_removed` — a key absent now, present before.

Nothing else in the row can produce an alert.

### 2. Diff the prose notes too — separately, and labelled

This is what catches the Sonnet 5 case. `<Note>` callouts are extracted, keyed
by their `id` attribute where present and by a content hash otherwise, and
diffed as text. Changes are emitted as class `advisory`, rendered in their own
section, and worded as "worth reading" rather than "the price changed".

Keeping these apart from price deltas is the point. An advisory is a
lower-confidence, higher-context signal; merging it into the price feed would
make the price feed untrustworthy.

### 3. A sanity gate, or the first vendor redesign lies to you

If Anthropic restructures its page, a naive parser returns zero rows, the differ
faithfully reports *every model removed*, and the site publishes a
confidently-wrong empty table. That is the failure mode that would destroy trust
in the tool, and it is guaranteed to happen eventually.

**Gate:** if a parse yields fewer than 50% of the previous snapshot's row count,
the run aborts. It does not commit, does not touch the site, and does not emit
price entries. It raises a distinct `parser-broken` alert naming the source and
both counts.

A first run has no previous snapshot; the gate requires a nonzero row count
instead, and every source declares a plausibility floor so a half-broken parse
cannot bootstrap a bad baseline.

## Backfill

Copilot's changelog is seeded from git. For each of the three YAML files, walk
`github/docs` commit history for that path, fetch each revision's blob, parse it,
diff consecutive revisions, and emit dated changelog entries with the commit
message and a link to the commit. The site therefore launches with real history
going back as far as those files exist, rather than being empty for months.

Anthropic has no equivalent archive. Its history starts at first run. The site
says so explicitly rather than implying the record is complete — a changelog
that silently starts mid-story is a changelog that misleads.

## Site

Single static page.

1. **Changelog**, newest first, grouped by date. Each entry: vendor badge, change
   class, model, old → new, and a source link.
2. **Advisories**, separately, newest first.
3. **Current tables**, one per source, as reference.
4. **Footer**: last-checked timestamp, and whether the last run was clean or
   gated. A silently dead Action must be visible on the page — an unchanging
   changelog looks identical to a working tool in a quiet month.

Atom feed at `site/feed.xml`, one entry per changelog date, so it can be read
without visiting.

## Notifications

All four channels, per request.

| Channel | Mechanism | Needs |
|---|---|---|
| Site | Pages, rebuilt each change | — |
| GitHub issue | `gh` in the Action, one issue per change batch | `GITHUB_TOKEN` (automatic) |
| Atom feed | `site/feed.xml` | — |
| Email | `smtplib` from the Action | `SMTP_*` repo secrets |

Email is built but **inert until secrets exist**: if `SMTP_HOST` is unset the
step is skipped with a log line, never an error. This keeps the Action green for
someone who never configures mail, while the code path stays live and tested.

`parser-broken` alerts use the same channels with a distinct title so they sort
separately and cannot be mistaken for a price change.

## Layout

```
llm-price-watch/
├── README.md
├── docs/specs/                    this file
├── data/
│   ├── anthropic.json             current snapshot
│   ├── copilot-*.json             current snapshots (3)
│   ├── changelog.json             accumulated dated entries
│   └── state.json                 last run status, for the footer
├── src/
│   ├── sources.py                 fetch + parse, per vendor
│   ├── diffing.py                 structured comparison + sanity gate
│   ├── changelog.py               append/load, dedupe
│   ├── site.py                    index.html + feed.xml
│   ├── notify.py                  issue body, email
│   ├── backfill.py                seed from github/docs git history
│   └── update.py                  entry point
├── tests/                         fixtures + unit tests
├── site/                          generated, committed, served by Pages
└── .github/workflows/update.yml   daily cron + workflow_dispatch
```

Python 3, one dependency (`pyyaml`). Everything else is stdlib — `urllib`,
`json`, `re`, `smtplib`, `html`. Rendering the page by hand rather than pulling
a static-site generator keeps the whole thing readable in one sitting and
removes an entire class of upgrade breakage.

## Non-goals

- No cost calculator. Vendors have those, and it would need a usage model.
- No historical price *charts*. Git history is the record; a chart is decoration
  until there is a year of data.
- No coverage of every vendor. See *Scope*.
- No LLM in the data path. Parsers must be deterministic — a probabilistic
  extractor that occasionally hallucinates a price is worse than no tool.
