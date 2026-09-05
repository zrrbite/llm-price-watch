#!/usr/bin/env python3
"""Answer "should I use this model?" from live pricing data.

Usage:
    check.py                      # everything worth knowing right now
    check.py "opus 5"             # verdict on one model
    check.py --json "sonnet"      # same, as JSON
    check.py --task "light work"  # what to use for a kind of work
    check.py --tier Lightweight   # same, when you have already judged the tier

Reads the digest published by llm-price-watch. Network failure is not fatal:
a cached copy is used when the fetch fails, and the age is always stated, so
an answer is never silently based on stale numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://zrrbite.github.io/llm-price-watch/advice.json"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "llm-price-check.json"
CACHE_TTL = 3600  # the source itself only updates twice a day
EXPENSIVE_RANK = 0.5  # in the dearer half of its tier

# Cheapest first. Used to break classification ties towards the cheaper tier,
# and to name the tier above when the recommended one may not be enough.
TIER_ORDER = ["Lightweight", "Versatile", "Powerful", "Frontier"]


def use_utf8_output() -> None:
    """Windows consoles default to a legacy codepage. Model names contain
    U+2264 ("GPT-5.4 (<= 272K)") and the output uses em dashes; printing
    either under cp1252 raises UnicodeEncodeError and takes down the whole
    answer — on the platform the skill explicitly tells people to run."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def load() -> tuple[dict, str]:
    """Return (data, provenance)."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(CACHE.read_text(encoding="utf-8")), "cached"
        except json.JSONDecodeError:
            pass
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "llm-price-check"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data), encoding="utf-8")
        return data, "live"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        if CACHE.exists():
            try:
                return json.loads(CACHE.read_text(encoding="utf-8")), f"stale cache ({exc})"
            except json.JSONDecodeError:
                pass
        raise SystemExit(f"could not reach {URL} and no usable cache: {exc}")


def days_until(iso: str) -> int | None:
    """Days from today to *iso*; negative if already past, None if unparseable."""
    try:
        return (date.fromisoformat(iso) - date.today()).days
    except (TypeError, ValueError):
        return None


# Words and version numbers, split apart wherever they meet, so "opus5",
# "opus 5" and "claude-opus-5" tokenise alike. A version keeps its dots:
# "5.4" must stay a single token to be comparable against "5".
TOKEN_RE = re.compile(r"\d+(?:\.\d+)*|[a-z]+")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall((text or "").lower())


def _is_version(token: str) -> bool:
    return token[:1].isdigit()


def _looseness(name_tokens: list[str], query_tokens: list[str]) -> int | None:
    """How loosely *query_tokens* matches *name_tokens*, or None for no match.

    Every query token must be accounted for, and version tokens compare
    exactly: "4" is not "4.5". That one rule is the fix for the old matcher,
    which stripped the dot and so answered "opus 4" with nine models, none of
    them singled out as the one actually asked for.

    The value returned is the count of leftover name tokens, so that "gpt 5.4"
    prefers GPT-5.4 over GPT-5.4 mini rather than ranking them as equals.
    """
    remaining = list(name_tokens)
    for query in query_tokens:
        for i, name in enumerate(remaining):
            if _is_version(query) or _is_version(name):
                hit = query == name
            else:
                hit = name.startswith(query) if len(query) > 1 else name == query
            if hit:
                del remaining[i]
                break
        else:
            return None
    return len(remaining)


# A hit only in the decorated name ("GPT-5.4 (> 272K)") is weaker than one in
# the base name and must never outrank it. Larger than any real token count.
DECORATED_PENALTY = 100


def find(models: list[dict], query: str, vendor: str | None = None) -> list[dict]:
    """Match on name, precisely. Returns every model matching equally well —
    "sonnet" legitimately means several, and one model sold by two vendors is
    two rows — but not the looser hits ranked behind them."""
    wanted = tokens(query)
    if not wanted:
        return []

    # Leftover tokens only decide between candidates when the query names a
    # version, i.e. when it is trying to pin one model down. A bare family
    # query is asking for the family: ranking "Kimi K3" above "Kimi K2.7 Code"
    # merely because its name is shorter would drop a model without saying so.
    pinning = any(_is_version(token) for token in wanted)

    scored: list[tuple[int, dict]] = []
    for model in models:
        if vendor and model["vendor"].lower() != vendor.lower():
            continue
        penalty = 0
        extras = _looseness(tokens(model.get("base_name") or model["name"]), wanted)
        if extras is None:
            extras = _looseness(tokens(model["name"]), wanted)
            if extras is None:
                continue
            penalty = DECORATED_PENALTY
        scored.append((penalty + (extras if pinning else 0), model))

    if not scored:
        return []
    best = min(score for score, _ in scored)
    return [model for score, model in scored if score == best]


def money(value) -> str:
    if value is None:
        return "?"
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return f"${text}"


def verdict(model: dict) -> list[str]:
    """The warnings, most consequential first. Empty means nothing to flag."""
    out = []

    retires = model.get("retires")
    if retires:
        replacement = f" — replacement {model['replacement']}" if model.get("replacement") else ""
        days = days_until(retires)
        if days is None:
            out.append(f"RETIRES {retires}{replacement}")
        elif days < 0:
            # Past tense matters: "RETIRES 2026-05-01" on a date four months
            # gone reads as a warning about something upcoming.
            out.append(f"RETIRED {retires}{replacement}")
        elif days == 0:
            out.append(f"RETIRES TODAY, {retires}{replacement}")
        else:
            out.append(f"RETIRES in {days} day(s), on {retires}{replacement}")

    if not model.get("available", True):
        out.append(f"NOT GENERALLY AVAILABLE ({model.get('status')})")

    if model.get("on_offer"):
        ends, days = model.get("offer_ends"), model.get("offer_days_left")
        if days is not None and days < 0:
            # The most actionable state: the discount is over but the published
            # price has not caught up. Whatever number you plan against here is
            # already wrong.
            out.append(
                f"PRICE ABOUT TO REVERT — the promotion ended {ends} "
                f"({abs(days)} day(s) ago) and the published price has not gone back up yet."
            )
        elif days is not None and days <= 7:
            when = "today" if days == 0 else f"in {days} day(s)"
            out.append(f"PROMO PRICE ENDING {when}, on {ends}. The price reverts after that.")
        elif ends:
            out.append(f"promo price until {ends} — not the standard rate")

    rank, size = model.get("rank_in_tier"), model.get("tier_size")
    if rank and size and size > 2 and rank / size > EXPENSIVE_RANK:
        out.append(f"dear for its class — {rank} of {size} by price in the {model['tier']} tier")

    for alt in model.get("cheaper_alternatives", [])[:2]:
        if alt.get("offer_lapsed"):
            note = " — WARNING: that price is a lapsed promotion and is about to rise"
        elif alt.get("on_offer"):
            note = " (itself on a promo price, so temporary)"
        else:
            note = ""
        out.append(
            f"cheaper same-tier option: {alt['name']} at {money(alt['blended'])} blended, "
            f"saves {alt['saves_percent']:.0f}%{note}"
        )

    return out


def describe(model: dict) -> str:
    head = (
        f"{model['name']} [{model['vendor']} / {model.get('tier') or 'untiered'}] "
        f"{money(model['input'])} in / {money(model['output'])} out per MTok "
        f"— {money(model['blended'])} blended"
    )
    if model.get("bargain"):
        b = model["bargain"]
        head += f"\n  GOOD VALUE: {b['percent_below']:.0f}% below the typical {b['compared_tier']} model"
    lines = verdict(model)
    if lines:
        head += "\n  " + "\n  ".join(lines)
    return head


# --- recommending by kind of work -------------------------------------------

# Deliberately plain vocabulary, and deliberately visible: both the chosen tier
# and the words that chose it are printed, so a wrong call can be seen and
# overridden with --tier rather than quietly believed.
TASK_WORDS: dict[str, tuple[str, ...]] = {
    "Lightweight": (
        "light", "simple", "trivial", "quick", "cheap", "cheapest", "budget",
        "bulk", "volume", "high volume", "classify", "classification", "label",
        "tag", "extract", "extraction", "summarise", "summarize", "summary",
        "autocomplete", "boilerplate", "format", "formatting", "lint", "rename",
        "triage", "routine", "menial", "batch",
    ),
    "Versatile": (
        "general", "everyday", "day to day", "normal", "standard", "typical",
        "chat", "code", "coding", "refactor", "refactoring", "test", "tests",
        "review", "script", "scripting", "feature", "bug", "bugfix", "docs",
        "documentation", "most work", "moderate", "medium",
    ),
    "Powerful": (
        "hard", "complex", "complicated", "difficult", "tricky", "deep",
        "debug", "debugging", "architecture", "architect", "design", "plan",
        "planning", "reason", "reasoning", "algorithm", "algorithms",
        "performance", "optimise", "optimize", "security", "audit", "migration",
        "migrate", "concurrency", "subtle",
    ),
    "Frontier": (
        "research", "novel", "hardest", "frontier", "state of the art",
        "cutting edge", "best", "highest quality", "no expense", "proof",
        "theory", "mathematical", "long horizon", "unsolved",
    ),
}


def classify(task: str) -> tuple[str, list[str]]:
    """Map a description of the work onto a capability tier.

    Returns (tier, matched words). No match is not a failure — Versatile is
    the honest default for unspecified work — but it is reported as a default
    rather than dressed up as a judgement.
    """
    words = set(tokens(task))
    phrase = " ".join(tokens(task))
    matched = {
        tier: sorted({w for w in vocab if (w in words) or (" " in w and w in phrase)})
        for tier, vocab in TASK_WORDS.items()
    }
    matched = {tier: hits for tier, hits in matched.items() if hits}
    if not matched:
        return "Versatile", []

    top = max(len(hits) for hits in matched.values())
    # Ties go to the cheaper tier. Overspending on work that did not need it is
    # the costlier of the two mistakes, and the tier above is printed anyway.
    for tier in TIER_ORDER:
        if len(matched.get(tier, ())) == top:
            return tier, matched[tier]
    raise AssertionError("unreachable")  # pragma: no cover


def _version_key(model: dict) -> tuple[int, ...]:
    """The model's version as comparable numbers: "Claude Opus 4.5" -> (4, 5).
    Empty when the name carries no version, which sorts it last."""
    versions = [t for t in tokens(model.get("base_name") or model["name"]) if _is_version(t)]
    if not versions:
        return ()
    return tuple(int(part) for part in versions[-1].split("."))


def _candidates(models: list[dict], tier: str, vendor: str | None = None) -> dict[str, list[dict]]:
    """Available models in *tier*, cheapest first, grouped by vendor. Vendors
    tier the same model differently — Haiku 4.5 is Lightweight at Anthropic and
    Versatile on Copilot — so these are grouped by vendor, never merged."""
    groups: dict[str, list[dict]] = {}
    for model in models:
        if model.get("tier") != tier or not model.get("available", True):
            continue
        if vendor and model["vendor"].lower() != vendor.lower():
            continue
        groups.setdefault(model["vendor"], []).append(model)
    for rows in groups.values():
        # Two passes, relying on a stable sort: price decides, and where price
        # ties the later version comes first. Anthropic prices Opus 4.5 through
        # Opus 5 identically, and heading that list with 4.5 because it happens
        # to come first in the feed is advice nobody wants. "Same price, newer"
        # is not a quality claim — this still knows nothing about capability.
        rows.sort(key=_version_key, reverse=True)
        rows.sort(key=lambda m: (m["blended"] is None, m["blended"] or 0))
    return groups


def _price_warnings(model: dict) -> list[str]:
    """The warnings that bear on a recommendation. The "cheaper alternative"
    lines are dropped: in a list already sorted by price they restate it."""
    return [line for line in verdict(model)
            if not line.startswith("cheaper same-tier option")]


def recommend(data: dict, tier: str, vendor: str | None = None, limit: int = 3) -> str:
    out = [f"RECOMMENDED TIER: {tier}"]
    groups = _candidates(data["models"], tier, vendor)
    if not groups:
        where = f" at {vendor}" if vendor else ""
        out.append(f"  (no available models in the {tier} tier{where})")
    for name in sorted(groups):
        out.append(f"\n{name} / {tier}")
        for i, model in enumerate(groups[name][:limit], 1):
            out.append(
                f"  {i}. {model['name']} — {money(model['blended'])} blended "
                f"({money(model['input'])} in / {money(model['output'])} out)"
            )
            for line in _price_warnings(model):
                out.append(f"       {line}")

    above = TIER_ORDER.index(tier) + 1 if tier in TIER_ORDER else len(TIER_ORDER)
    if above < len(TIER_ORDER):
        step_up = TIER_ORDER[above]
        higher = _candidates(data["models"], step_up, vendor)
        if higher:
            out.append(f"\nIf {tier} is not enough, the cheapest step up ({step_up}):")
            for name in sorted(higher):
                best = higher[name][0]
                out.append(f"  {name}: {best['name']} at {money(best['blended'])} blended")
    return "\n".join(out)


def _is_lapsed(offer: dict) -> bool:
    if offer.get("lapsed"):
        return True
    days = offer.get("days_left")
    return days is not None and days < 0


def overview(data: dict) -> str:
    # Live and lapsed must not share a heading. A promotion whose price is
    # about to rise is not an offer; listing it as one, with a "-1d left"
    # countdown, invites you to plan around a number that has already expired.
    live = [o for o in data["offers"] if not _is_lapsed(o)]
    lapsed = [o for o in data["offers"] if _is_lapsed(o)]

    out = []
    if lapsed:
        out.append("PRICE ABOUT TO REVERT — promotion over, published price not yet updated:")
        for offer in lapsed:
            names = ", ".join(offer["models"]) or offer["vendor"]
            out.append(f"  {names} — ended {offer.get('expires')}")
        out.append("")

    out.append("ON OFFER NOW:")
    if not live:
        out.append("  (nothing)")
    for offer in live:
        days = offer.get("days_left")
        when = f"{days}d left" if days is not None else "no end date"
        names = ", ".join(offer["models"]) or offer["vendor"]
        out.append(f"  [{when}] {names} — {offer['text'][:150]}")

    if data.get("retiring_soon"):
        out.append("\nRETIRING SOON:")
        for row in data["retiring_soon"]:
            out.append(f"  {row['model']} in {row['days_left']}d ({row['date']}) -> {row.get('alternative')}")

    out.append("\nCHEAPEST PER TIER:")
    for tier, info in data["cheapest_by_tier"].items():
        out.append(f"  {tier}: {info['model']} at {money(info['blended'])} blended")
    return "\n".join(out)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check.py",
        description="Check live LLM pricing before committing to a model.",
    )
    parser.add_argument("query", nargs="*", help='model name, e.g. "opus 5"')
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="machine-readable output")
    parser.add_argument("--task", metavar="TEXT",
                        help='describe the work, e.g. "light work"; picks a tier')
    parser.add_argument("--tier", choices=TIER_ORDER,
                        help="recommend within this tier, skipping classification")
    parser.add_argument("--vendor", metavar="NAME",
                        help="restrict to one vendor, e.g. Anthropic or Copilot")
    parser.add_argument("--limit", type=int, default=3, metavar="N",
                        help="models listed per vendor when recommending (default 3)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    data, provenance = load()

    tier, matched = None, []
    if args.tier:
        tier = args.tier
    elif args.task:
        tier, matched = classify(args.task)

    if args.as_json:
        if tier:
            groups = _candidates(data["models"], tier, args.vendor)
            payload = {
                "generated": data["generated"], "provenance": provenance,
                "task": args.task, "tier": tier, "matched_words": matched,
                "classified": bool(args.task and not args.tier),
                "recommendations": {v: rows[:args.limit] for v, rows in groups.items()},
            }
        elif args.query:
            payload = {"generated": data["generated"], "provenance": provenance,
                       "matches": find(data["models"], " ".join(args.query), args.vendor)}
        else:
            payload = data
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"# llm-price-watch, generated {data['generated']} ({provenance})")
    print(f"# blended = {data['blended_formula']}, {data['units']}\n")

    if tier:
        if args.task:
            reason = (f"matched {', '.join(repr(w) for w in matched)}" if matched
                      else "no wording matched a tier, so this is the default, not a judgement")
            print(f'TASK: "{args.task}"  ({reason})')
            print("Override with --tier if that reads wrong. "
                  f"Tiers, cheapest first: {' < '.join(TIER_ORDER)}\n")
        print(recommend(data, tier, args.vendor, args.limit))
        return 0

    if not args.query:
        print(overview(data))
        return 0

    query = " ".join(args.query)
    matches = find(data["models"], query, args.vendor)
    if not matches:
        print(f"No model matching {query!r}. This tracks Anthropic's API and the models "
              f"GitHub Copilot offers — a model outside both will not be here.")
        return 1
    for model in matches:
        print(describe(model))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
