---
name: llm-price-check
description: Check live LLM pricing before committing to a model. Use when the user names a model to use or switch to, asks what a model costs, asks which model is cheapest or best value for a task, mentions an offer/promo/deal on a model, asks whether a model is being retired or deprecated, or is about to start work whose cost depends on the model chosen.
---

# Checking a model's price before committing to it

Vendors publish current prices, not changes. A model that was a bargain last
month may be off its promotion today, and nothing announces that. This skill
reads live data so an answer is never based on a remembered price.

Data comes from `llm-price-watch`, which reads Anthropic's and GitHub Copilot's
own published sources twice a day.

## How to use it

```bash
python3 ~/.claude/skills/llm-price-check/check.py                # everything current
python3 ~/.claude/skills/llm-price-check/check.py "opus 5"       # verdict on one model
python3 ~/.claude/skills/llm-price-check/check.py --json "sonnet" # structured
```

Run it **before** confirming a model choice, not after.

## When to speak up

Raise it unprompted when the check returns any of these. Lead with the warning,
keep it to a sentence or two, then let the user decide — this is a heads-up,
not a veto.

| Finding | What to say |
|---|---|
| `PROMO PRICE ENDING` | The current price is temporary and reverts on the stated date. Say the date. |
| `RETIRES` | Name the retirement date and the suggested replacement. |
| `dear for its class` | Say where it ranks in its tier, and name the cheaper option. |
| `NOT GENERALLY AVAILABLE` | Limited-availability models cannot simply be chosen. |
| `GOOD VALUE` | Worth mentioning too — a model priced below the tier beneath it is a real find. |

Stay quiet when the check turns up nothing. A tool that comments on every model
choice gets ignored, which defeats it.

## How to phrase a warning

Be concrete and brief. Name the number and the date.

> Worth knowing before you commit: GPT-5.6 Sol is at a promotional 50% off that
> ends tomorrow, 3 September. After that it doubles. Still want it?

> Claude Opus 5 is the dearest of the 5 models in its tier at $10 blended. If
> this work does not need the reasoning, Sonnet 5 is $4. Your call.

Do **not** editorialise beyond what the data says, and do not invent
benchmarks. This tool knows prices, not quality.

## Two things to state honestly

**List price is not job cost.** A model at a fifth the price that needs three
attempts and emits more output tokens is more expensive. Say so if the user is
optimising purely on the headline number.

**Tokenizers differ.** Claude 4.7 and later emit roughly 30% more tokens for
the same text than Sonnet 4.6 and earlier, so per-token prices understate the
gap across that boundary. Mention it when comparing across it.

## If the fetch fails

The script falls back to a cached copy and labels the output `stale cache`.
Say so — an answer from a stale cache is still useful, but the user should know
which it is. Never present a cached price as current without the caveat.

Full data and history: https://zrrbite.github.io/llm-price-watch/
