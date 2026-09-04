#!/usr/bin/env python3
"""Answer "should I use this model?" from live pricing data.

Usage:
    check.py                 # everything worth knowing right now
    check.py "opus 5"        # verdict on one model
    check.py --json "sonnet" # same, as JSON

Reads the digest published by llm-price-watch. Network failure is not fatal:
a cached copy is used when the fetch fails, and the age is always stated, so
an answer is never silently based on stale numbers.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://zrrbite.github.io/llm-price-watch/advice.json"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "llm-price-check.json"
CACHE_TTL = 3600  # the source itself only updates twice a day
EXPENSIVE_RANK = 0.5  # in the dearer half of its tier


def load() -> tuple[dict, str]:
    """Return (data, provenance)."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(CACHE.read_text()), "cached"
        except json.JSONDecodeError:
            pass
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "llm-price-check"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(data))
        return data, "live"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        if CACHE.exists():
            try:
                return json.loads(CACHE.read_text()), f"stale cache ({exc})"
            except json.JSONDecodeError:
                pass
        raise SystemExit(f"could not reach {URL} and no usable cache: {exc}")


def normalise(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum())


def find(models: list[dict], query: str) -> list[dict]:
    """Match on name, loosely. Returns every plausible hit rather than
    guessing between them — 'sonnet' legitimately means several models."""
    want = normalise(query)
    exact = [m for m in models if normalise(m["name"]) == want or normalise(m["base_name"]) == want]
    if exact:
        return exact
    return [m for m in models if want in normalise(m["name"])]


def money(value) -> str:
    if value is None:
        return "?"
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return f"${text}"


def verdict(model: dict) -> list[str]:
    """The warnings, most consequential first. Empty means nothing to flag."""
    out = []

    if model.get("retires"):
        out.append(
            f"RETIRES {model['retires']}"
            + (f" — replacement {model['replacement']}" if model.get("replacement") else "")
        )

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
        note = " (itself on a promo price)" if alt.get("on_offer") else ""
        out.append(f"cheaper same-tier option: {alt['name']} at {money(alt['blended'])} blended, saves {alt['saves_percent']:.0f}%{note}")

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


def overview(data: dict) -> str:
    out = ["ON OFFER NOW:"]
    if not data["offers"]:
        out.append("  (nothing)")
    for offer in data["offers"]:
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


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv[1:]
    data, provenance = load()

    if as_json:
        payload = (
            {"generated": data["generated"], "provenance": provenance,
             "matches": find(data["models"], " ".join(args))}
            if args else data
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"# llm-price-watch, generated {data['generated']} ({provenance})")
    print(f"# blended = {data['blended_formula']}, {data['units']}\n")

    if not args:
        print(overview(data))
        return 0

    query = " ".join(args)
    matches = find(data["models"], query)
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
