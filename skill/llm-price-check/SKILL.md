---
name: llm-price-check
description: Check live LLM pricing before committing to a model, and recommend one for a kind of work. Use when the user names a model to use or switch to, asks what a model costs, asks which model is cheapest or best value for a task, describes the work rather than naming a model ("something for light work", "what should I use for hard debugging", "cheapest thing that can do bulk classification"), mentions an offer/promo/deal on a model, asks whether a model is being retired or deprecated, or is about to start work whose cost depends on the model chosen.
argument-hint: "[model name, e.g. opus 5 — or a kind of work, e.g. light work]"
---

# Checking a model's price before committing to it

Vendors publish current prices, not changes. A model that was a bargain last
month may be off its promotion today, and nothing announces that. Read live data
rather than a remembered price.

Data comes from `llm-price-watch`, which reads Anthropic's and GitHub Copilot's
own published sources twice a day.

## Two ways to get the data

**Preferred — run the script** (relative to this skill folder). It does the
matching and the judgement for you:

```bash
python3 ./check.py                     # everything current
python3 ./check.py "opus 5"            # verdict on one model
python3 ./check.py --json "sonnet"
python3 ./check.py --task "light work"  # what to use for a kind of work
python3 ./check.py --tier Powerful      # same, tier already decided
python3 ./check.py --vendor Anthropic "opus 5"   # one vendor only
```

On Windows use `python ./check.py` — `python3` is frequently not on PATH there.
The script needs only the standard library, no install step. If neither
interpreter is available, or if the script cannot reach the network, use the
fetch route below instead of reporting failure.

**If you cannot execute scripts**, fetch one of these instead and reason over
it. They are sized so the cheap one is genuinely cheap:

| URL | Cost | Gives you |
|---|---|---|
| `https://zrrbite.github.io/llm-price-watch/brief.txt` | ~180 tokens | cheapest per class, what is on offer, what is about to revert or retire |
| `https://zrrbite.github.io/llm-price-watch/models.tsv` | ~820 tokens | one line per model: price, capability class, offer state |
| `https://zrrbite.github.io/llm-price-watch/advice.json` | ~12,000 tokens | full detail: rank in class, cheaper alternatives, offer text |

Start with `brief.txt`. Only fetch `models.tsv` if you need to compare specific
models, and `advice.json` only if you need why-cheaper detail.

Run the check **before** confirming a model choice, not after.

## Naming a model

Version numbers are matched exactly, so name the one you mean. `"opus 4"` is
Opus 4 alone, not Opus 4.1 through 4.8; ask for `"opus 4.5"` to get that one.
Drop the version to get the whole family: `"opus"` returns all of them, `"kimi"`
returns both.

Two rows for one model is normal and not a bug — the same model priced by two
vendors, or one model priced in two context-window bands (`≤ 272K` / `> 272K`).
Both prices are real. Narrow with `--vendor Anthropic` or `--vendor Copilot`
when the user can only reach one of them.

## Recommending a model for a kind of work

When the user describes the work instead of naming a model — "light work",
"something cheap for bulk tagging", "the hard debugging" — recommend rather
than quote.

**You classify, then ask for the tier.** Judge which tier the work needs and
pass `--tier Lightweight|Versatile|Powerful|Frontier`. You read the user's
actual situation; the script only reads keywords.

Use `--task "their words"` when you would rather see the script's own reading
first, or as a starting point you then override. It prints the tier it chose
and the words that chose it, so you can see when it has misread. It is a
keyword match, not a judgement — treat it as a suggestion.

Either way the output ranks the tier's available models by price per vendor,
drops retired ones, flags any whose price is about to move, and names the
cheapest model one tier up in case the recommended tier is not enough.

Two things the tiers do not tell you:

- **Tiers are the vendor's own labels, and vendors disagree.** Claude Haiku 4.5
  is Lightweight at Anthropic and Versatile on Copilot. Same model, same price,
  different shelf. Recommend within a vendor, not across.
- **This still knows nothing about capability.** Within a tier it ranks on
  price alone, newer version first only where prices tie. Do not present the
  cheapest model in a tier as the best one in it.

## When to speak up

Raise it unprompted when any of these is true. Lead with the warning, keep it to
a sentence or two, then let the user decide — this is a heads-up, not a veto.

| Finding | What to say |
|---|---|
| `LAPSED` / `PRICE ABOUT TO REVERT` | The promotion has already ended and the published price has not caught up. It is going to rise. Say the end date. |
| `PROMO PRICE ENDING` | The current price is temporary. Say the date it reverts. |
| `RETIRES` | Name the retirement date and the suggested replacement. |
| `dear for its class` | Say where it ranks in its class, and name the cheaper option. |
| `NOT GENERALLY AVAILABLE` | Limited-availability models cannot simply be chosen. |
| `GOOD VALUE` | Worth mentioning too — a model priced below the class beneath it is a real find. |

Stay quiet when the check turns up nothing. A tool that comments on every model
choice gets ignored, which defeats it.

## How to phrase a warning

Be concrete and brief. Name the number and the date.

> Worth knowing before you commit: GPT-5.6 Sol's 50% promotion ended on
> 3 September and the published price has not reverted yet. It will. Still want
> to build on that number?

> Claude Opus 5 is the dearest of the 5 models in its class at $10 blended. If
> this work does not need the reasoning, Sonnet 5 is $4. Your call.

Do **not** editorialise beyond what the data says, and do not invent benchmarks.
This knows prices, not quality.

## Two things to state honestly

**List price is not job cost.** A model at a fifth the price that needs three
attempts and emits more output tokens is more expensive. Say so if the user is
optimising purely on the headline number.

**Tokenizers differ.** Claude 4.7 and later emit roughly 30% more tokens for the
same text than Sonnet 4.6 and earlier, so per-token prices understate the gap
across that boundary. Mention it when comparing across it.

## Reading the numbers

`blended` is `0.75*input + 0.25*output` per million tokens — a single comparable
figure weighted for the usual shape of chat and coding work. `class` is the
vendor's own capability tier (Lightweight / Versatile / Powerful / Frontier), so
price can be judged against what you get.

## If the fetch fails

The script falls back to a cached copy and labels the output `stale cache`. Say
so — an answer from a stale cache is still useful, but the user should know
which it is. Never present a cached price as current without the caveat.

Full data and history: https://zrrbite.github.io/llm-price-watch/
