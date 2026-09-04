"""Turn snapshots and changelog into an at-a-glance overview.

Two questions a changelog cannot answer on its own: *what is discounted right
now*, and *what is the cheap option for a given kind of work*. Both are derived
here, from data, with the rule stated wherever a judgement is involved.

Nothing in this module invents a fact. An offer exists because a vendor wrote
it down; a value pick is arithmetic over published prices. Anything genuinely
editorial lives in ``data/picks.yml``, is version controlled, and is rendered
with live prices so it cannot quietly go stale.
"""

from __future__ import annotations

import re
import statistics
from datetime import date, datetime, timedelta, timezone

import diffing

# Weighting for the single "typical mix" number. Chat and coding workloads send
# more than they receive, so input is weighted 3:1 against output. Stated on
# the page, because any single number here is a modelling choice.
INPUT_WEIGHT = 0.75
OUTPUT_WEIGHT = 0.25

# How long a price cut stays interesting.
CUT_WINDOW_DAYS = 180
# What counts as "act on this soon".
EXPIRING_DAYS = 30
RETIRING_DAYS = 45
# How far below the tier beneath it a model must sit to be worth calling out.
# Without a floor the list fills with models scraping in at 0-2% under the
# median, which is noise dressed as a finding.
BARGAIN_MARGIN = 0.05

# Anthropic states promotions in prose. These are the phrasings that mark a
# note as an offer rather than as documentation.
#
# Deliberately narrow. Bare "offer" matches the Bedrock private-offer
# boilerplate, and bare "discount" matches the Claude Consumption Units legal
# text ("after application of any discounts") -- both were false positives on
# the real page. A section that cries wolf is a section you stop reading.
OFFER_PATTERNS = re.compile(
    r"introductory pric|promotional pric|\d+\s*%\s*off|free hours|"
    r"at no additional cost|free of charge|will not occur",
    re.I,
)

TIER_ORDER = ["Lightweight", "Versatile", "Powerful", "Frontier"]

# Anthropic publishes no category field, so family name stands in. This is a
# mapping we control; it is stable because the families are.
ANTHROPIC_TIERS = [
    ("haiku", "Lightweight"),
    ("sonnet", "Versatile"),
    ("opus", "Powerful"),
    ("fable", "Frontier"),
    ("mythos", "Frontier"),
]


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def blended(values: dict) -> float | None:
    """A single comparable number, per the stated 3:1 input:output weighting."""
    inp, out = values.get("input"), values.get("output")
    if not isinstance(inp, (int, float)) or isinstance(inp, bool):
        return None
    if not isinstance(out, (int, float)) or isinstance(out, bool):
        return None
    return INPUT_WEIGHT * float(inp) + OUTPUT_WEIGHT * float(out)


def _version_key(name: str) -> list:
    """Natural sort key, so "Opus 5" beats "Opus 4.5" on a price tie."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def _by_source(snapshots: list[dict]) -> dict[str, dict]:
    return {s["source"]: s for s in snapshots}


def _is_unavailable(values: dict) -> bool:
    """Models you cannot simply choose today.

    Retired ones are obvious. Limited-availability ones matter just as much:
    Claude Mythos is the cheapest thing in its tier on paper, and recommending
    it would send you at a model you have to be granted access to.
    """
    status = f"{values.get('status', '')} {values.get('release_status', '')}".lower()
    return any(word in status for word in ("retired", "deprecat", "limited availability"))


# --------------------------------------------------------------------------
# offers


def find_offers(snapshots: list[dict], changelog: list[dict], today: date | None = None) -> list[dict]:
    """Everything currently cheaper than usual, and why.

    Three kinds, in descending order of how firmly the vendor stated it:
    an explicit promotional footnote, a promotional note in prose, and a price
    cut that is still in force.
    """
    today = today or _today()
    sources = _by_source(snapshots)
    offers: list[dict] = []

    # 1. Copilot's promotional footnotes: explicit, dated, vendor-authored.
    copilot = sources.get("copilot-pricing")
    if copilot:
        for note in copilot.get("advisories", []):
            models = sorted(
                {
                    r["key"].split(" (")[0]
                    for r in copilot.get("rows", [])
                    if r.get("values", {}).get("offer") == note["key"]
                }
            )
            expires = note.get("expires")
            days_left = _days_between(today, expires)
            # A promotion past its end date whose price has NOT yet reverted is
            # the most actionable state there is, and it used to be the worst
            # handled: dropping it here let recent_price_cuts pick the same
            # model up and report the promotional price as a durable cut —
            # exactly backwards, in the section you would act on. Keep it, and
            # say what it is.
            lapsed = bool(expires and expires < today.isoformat())
            text = note["text"]
            if lapsed:
                text = (
                    f"LAPSED — this promotion ended {expires} but the published price has "
                    f"not reverted yet. Expect it to. Original terms: {text}"
                )
            offers.append(
                {
                    "vendor": "Copilot",
                    "kind": "lapsed promotion" if lapsed else "promotion",
                    "lapsed": lapsed,
                    "models": models,
                    "text": text,
                    "expires": expires,
                    "days_left": days_left,
                    "source_url": copilot.get("url"),
                }
            )

    # 2. Anthropic states promotions in prose notes.
    anthropic = sources.get("anthropic")
    if anthropic:
        for note in anthropic.get("advisories", []):
            if not OFFER_PATTERNS.search(note.get("text", "")):
                continue
            offers.append(
                {
                    "vendor": "Anthropic",
                    "kind": "promotion",
                    "models": [],
                    "text": note["text"],
                    "expires": None,
                    "days_left": None,
                    "source_url": anthropic.get("url"),
                }
            )

    # A model under an explicit promotion will also show up as a price cut,
    # because the promotion is what cut the price. Report it once, as the
    # promotion: that version carries the end date, which is the part that
    # actually changes what you would do about it.
    promoted = {m for o in offers for m in o["models"]}
    for cut in recent_price_cuts(snapshots, changelog, today):
        if any(m in promoted for m in cut["models"]):
            continue
        offers.append(cut)

    # Soonest deadline first; undated last.
    offers.sort(key=lambda o: (o.get("days_left") is None, o.get("days_left") or 0))
    return offers


def _days_between(today: date, iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) - today).days
    except ValueError:
        return None


def recent_price_cuts(
    snapshots: list[dict], changelog: list[dict], today: date | None = None,
    window_days: int = CUT_WINDOW_DAYS,
) -> list[dict]:
    """Price cuts from the changelog that are *still in force*.

    A cut that was later reversed is not an offer, so each candidate is checked
    against the current snapshot before it is reported.
    """
    today = today or _today()
    cutoff = (today - timedelta(days=window_days)).isoformat()

    current: dict[tuple[str, str], dict] = {}
    for snapshot in snapshots:
        for row in snapshot.get("rows", []):
            current[(snapshot["source"], row["key"])] = row.get("values", {})

    # Keyed by base model name, not by row key: a model priced per context tier
    # would otherwise report the same cut once per tier.
    best: dict[tuple[str, str], dict] = {}
    for entry in changelog:
        if entry.get("class") != diffing.PRICE_CHANGED or entry.get("date", "") < cutoff:
            continue
        row_key = entry.get("key", "")
        source = entry.get("source", "")
        values_now = current.get((source, row_key))
        if values_now is None:
            continue  # model is gone; not an offer
        key = (source, row_key.split(" (")[0])
        for field in entry.get("fields", []):
            name = field.get("field")
            old, new = field.get("old"), field.get("new")
            # Headline rates only. A cheaper cache-read is real but it is not
            # what anyone means by "on offer", and it crowds out what is.
            if name not in {"input", "output"} or not (_is_number(old) and _is_number(new)):
                continue
            if new >= old or old == 0:
                continue
            now = values_now.get(name)
            if not _is_number(now) or now > new:
                continue  # reversed since
            pct = (old - new) / old * 100
            record = best.get(key)
            if record is None or pct > record["percent"]:
                best[key] = {
                    "vendor": "Anthropic" if source == "anthropic" else "Copilot",
                    "kind": "price cut",
                    "models": [key[1]],
                    "field": name,
                    "old": old,
                    "new": new,
                    "percent": pct,
                    "date": entry.get("date"),
                    "expires": None,
                    "days_left": None,
                    "text": (
                        f"{name} fell {pct:.0f}% from {diffing.fmt_value(old, name)} to "
                        f"{diffing.fmt_value(new, name)} on {entry.get('date')}, and is still there."
                    ),
                }
    return sorted(best.values(), key=lambda o: -o["percent"])


def expiring_soon(offers: list[dict], days: int = EXPIRING_DAYS) -> list[dict]:
    return [o for o in offers if o.get("days_left") is not None and 0 <= o["days_left"] <= days]


def retiring_soon(snapshots: list[dict], today: date | None = None, days: int = RETIRING_DAYS) -> list[dict]:
    """Models with a retirement date inside the window, or just past it."""
    today = today or _today()
    snapshot = _by_source(snapshots).get("copilot-deprecations")
    if not snapshot:
        return []
    out = []
    for row in snapshot.get("rows", []):
        when = row.get("values", {}).get("retirement_date")
        left = _days_between(today, when)
        if left is None or left < 0 or left > days:
            continue
        out.append(
            {
                "model": row["key"],
                "date": when,
                "days_left": left,
                "alternative": row.get("values", {}).get("suggested_alternative"),
            }
        )
    return sorted(out, key=lambda r: r["days_left"])


# --------------------------------------------------------------------------
# value


def _tier_for(source: str, key: str, values: dict) -> str | None:
    if source == "copilot-pricing":
        category = values.get("category")
        return str(category) if category in TIER_ORDER else None
    if source == "anthropic":
        low = key.lower()
        for needle, tier in ANTHROPIC_TIERS:
            if needle in low:
                return tier
    return None


def value_table(snapshots: list[dict]) -> list[dict]:
    """Cheapest live model per vendor and capability tier, by blended cost.

    Retired, deprecated and limited-availability models are excluded — they are
    not something you can choose today, and leaving them in would make the
    cheap column a list of things you cannot use.
    """
    rows: list[dict] = []
    for snapshot in snapshots:
        source = snapshot.get("source")
        if source not in {"anthropic", "copilot-pricing"}:
            continue
        vendor = "Anthropic" if source == "anthropic" else "Copilot"
        grouped: dict[str, list[dict]] = {}
        for row in snapshot.get("rows", []):
            values = row.get("values", {})
            if _is_unavailable(values):
                continue
            cost = blended(values)
            if cost is None:
                continue
            tier = _tier_for(source, row["key"], values)
            if not tier:
                continue
            grouped.setdefault(tier, []).append(
                {
                    "model": row["key"],
                    "blended": cost,
                    "input": values.get("input"),
                    "output": values.get("output"),
                    "offer": values.get("offer"),
                }
            )

        for tier, candidates in grouped.items():
            # Two stable passes: newest first, then cheapest first. On a price
            # tie the newer model survives, so an identically priced Opus 4.5
            # does not shadow Opus 5.
            candidates.sort(key=lambda c: _version_key(c["model"]), reverse=True)
            candidates.sort(key=lambda c: c["blended"])
            rows.append(
                {
                    "vendor": vendor,
                    "tier": tier,
                    "cheapest": candidates[0],
                    "runners_up": candidates[1:3],
                    "count": len(candidates),
                }
            )

    rows.sort(key=lambda r: (TIER_ORDER.index(r["tier"]) if r["tier"] in TIER_ORDER else 99, r["vendor"]))
    return rows


# --------------------------------------------------------------------------
# bargains: models priced below the tier beneath them


def find_bargains(snapshots: list[dict], offers: list[dict] | None = None) -> list[dict]:
    """Models costing less than the typical model of the tier below them.

    This is the question a price table cannot answer: not "what is cheapest"
    but "what is underpriced for what it is". A Powerful-tier model going for
    less than the median Versatile-tier model is a better deal than anything
    the cheapest-per-tier table will show you, because the comparison that
    matters is against the class you would otherwise settle for.

    Where the low price comes from a promotion, that is reported alongside —
    an anomaly with an expiry date is a different proposition from a permanent
    one, and reporting them the same way would be misleading.
    """

    # A dated promotion beats an undated price cut when both name the same
    # model: the deadline is the part that changes what you would do.
    offers_by_ref: dict[str, dict] = {}
    for offer in offers or []:
        for model in offer.get("models", []):
            existing = offers_by_ref.get(model)
            if existing is None or (not existing.get("expires") and offer.get("expires")):
                offers_by_ref[model] = offer

    # vendor -> tier -> [(model, blended, offer_ref)]
    grouped: dict[str, dict[str, list[dict]]] = {}
    for snapshot in snapshots:
        source = snapshot.get("source")
        if source not in {"anthropic", "copilot-pricing"}:
            continue
        vendor = "Anthropic" if source == "anthropic" else "Copilot"
        for row in snapshot.get("rows", []):
            values = row.get("values", {})
            if _is_unavailable(values):
                continue
            cost = blended(values)
            tier = _tier_for(source, row["key"], values)
            if cost is None or not tier:
                continue
            grouped.setdefault(vendor, {}).setdefault(tier, []).append(
                {"model": row["key"], "blended": cost, "offer": values.get("offer")}
            )

    bargains: list[dict] = []
    for vendor, tiers in grouped.items():
        for index, tier in enumerate(TIER_ORDER):
            if index == 0 or tier not in tiers:
                continue
            below = TIER_ORDER[index - 1]
            if below not in tiers or not tiers[below]:
                continue
            reference = statistics.median(c["blended"] for c in tiers[below])
            if reference <= 0:
                continue
            for candidate in tiers[tier]:
                if candidate["blended"] > reference * (1 - BARGAIN_MARGIN):
                    continue
                cheaper_than = sum(1 for c in tiers[below] if c["blended"] > candidate["blended"])
                base = candidate["model"].split(" (")[0]
                offer = offers_by_ref.get(base)
                bargains.append(
                    {
                        "vendor": vendor,
                        "model": candidate["model"],
                        "tier": tier,
                        "compared_tier": below,
                        "blended": candidate["blended"],
                        "reference": reference,
                        "ratio": candidate["blended"] / reference,
                        "cheaper_than": cheaper_than,
                        "of": len(tiers[below]),
                        "promo": bool(candidate.get("offer")),
                        "expires": (offer or {}).get("expires"),
                        "days_left": (offer or {}).get("days_left"),
                    }
                )

    # Biggest anomaly first: furthest below the tier it is undercutting.
    bargains.sort(key=lambda b: b["ratio"])
    return bargains


# --------------------------------------------------------------------------
# machine-readable digest


def build_advice(snapshots: list[dict], changelog: list[dict], today: date | None = None) -> dict:
    """A compact digest for programmatic consumers.

    The site answers a human reading it. This answers a tool asking about one
    model: is it on offer, is it dear for what it is, is it about to retire,
    and what would be cheaper. Everything here is already computed for the
    page — emitting it as JSON costs nothing and saves consumers from scraping
    HTML, which would break the moment the page is restyled.
    """
    today = today or _today()
    offers = find_offers(snapshots, changelog, today)
    bargains = {b["model"]: b for b in find_bargains(snapshots, offers)}
    retirements = {
        r["key"]: r.get("values", {})
        for s in snapshots if s.get("source") == "copilot-deprecations"
        for r in s.get("rows", [])
    }

    offer_by_model: dict[str, dict] = {}
    for offer in offers:
        for name in offer.get("models", []):
            existing = offer_by_model.get(name)
            if existing is None or (not existing.get("expires") and offer.get("expires")):
                offer_by_model[name] = offer

    # Tier populations, so "expensive" can be stated relative to peers rather
    # than as a bare number nobody can calibrate against.
    tiers: dict[tuple[str, str], list[dict]] = {}
    models: list[dict] = []

    for snapshot in snapshots:
        source = snapshot.get("source")
        if source not in {"anthropic", "copilot-pricing"}:
            continue
        vendor = "Anthropic" if source == "anthropic" else "Copilot"
        for row in snapshot.get("rows", []):
            values = row.get("values", {})
            cost = blended(values)
            if cost is None:
                continue
            base = row["key"].split(" (")[0]
            offer = offer_by_model.get(base)
            # Retirements come from Copilot's deprecation list and are a
            # Copilot fact. GitHub dropping Claude Opus 4.5 says nothing about
            # whether Anthropic still sells it — and it does.
            retire = retirements.get(base, {}) if vendor == "Copilot" else {}
            entry = {
                "name": row["key"],
                "base_name": base,
                "vendor": vendor,
                "tier": _tier_for(source, row["key"], values),
                "input": values.get("input"),
                "output": values.get("output"),
                "blended": round(cost, 4),
                "available": not _is_unavailable(values),
                "status": values.get("status") or values.get("release_status"),
                "on_offer": bool(values.get("offer")) or bool(offer and offer.get("expires")),
                "offer_ends": (offer or {}).get("expires"),
                "offer_days_left": (offer or {}).get("days_left"),
                "offer_text": (offer or {}).get("text"),
                "retires": retire.get("retirement_date"),
                "replacement": retire.get("suggested_alternative"),
            }
            bargain = bargains.get(row["key"])
            if bargain:
                entry["bargain"] = {
                    "compared_tier": bargain["compared_tier"],
                    "percent_below": round((1 - bargain["ratio"]) * 100, 1),
                }
            models.append(entry)
            if entry["tier"] and entry["available"]:
                tiers.setdefault((vendor, entry["tier"]), []).append(entry)

    # Rank within tier, and name the cheaper same-class options. This is the
    # part that lets a consumer say "that is dear, and here is what is not".
    for (_vendor, _tier), peers in tiers.items():
        peers.sort(key=lambda m: m["blended"])
        for index, model in enumerate(peers):
            model["rank_in_tier"] = index + 1
            model["tier_size"] = len(peers)
            # Another context tier of the same model is not an alternative;
            # suggesting it reads as advice and is really just arithmetic.
            cheaper = [
                p for p in peers
                if p["blended"] < model["blended"] * 0.9
                and p["base_name"] != model["base_name"]
            ]
            model["cheaper_alternatives"] = [
                {
                    "name": p["name"],
                    "blended": p["blended"],
                    "saves_percent": round((1 - p["blended"] / model["blended"]) * 100, 1),
                    "on_offer": p["on_offer"],
                    # Without this, the cheapest alternative can be one whose
                    # promotional price has already expired — recommending a
                    # saving that is about to evaporate.
                    "offer_lapsed": (p.get("offer_days_left") or 0) < 0,
                }
                for p in cheaper[:3]
            ]

    return {
        "generated": utcnow(),
        "site": "https://zrrbite.github.io/llm-price-watch/",
        "units": "USD per million tokens",
        "blended_formula": f"{INPUT_WEIGHT}*input + {OUTPUT_WEIGHT}*output",
        "models": sorted(models, key=lambda m: (m["vendor"], m["name"])),
        "offers": [
            {
                "vendor": o["vendor"], "kind": o["kind"], "models": o["models"],
                "lapsed": bool(o.get("lapsed")),
                "expires": o.get("expires"), "days_left": o.get("days_left"),
                "text": o.get("text"),
            }
            for o in offers
        ],
        "retiring_soon": retiring_soon(snapshots, today),
        "cheapest_by_tier": {
            f"{v['vendor']}/{v['tier']}": {
                "model": v["cheapest"]["model"], "blended": round(v["cheapest"]["blended"], 4)
            }
            for v in value_table(snapshots)
        },
    }


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# editorial picks


def resolve_picks(picks: list[dict], snapshots: list[dict]) -> list[dict]:
    """Attach live prices to hand-written picks, and flag any that went stale.

    A recommendation naming a model and a price rots the moment either changes.
    Rather than trust the text, the price is looked up every build and the pick
    is marked stale if the model has vanished or its cost has moved materially
    since the pick was written.
    """
    current: dict[tuple[str, str], dict] = {}
    for snapshot in snapshots:
        for row in snapshot.get("rows", []):
            current[(snapshot["source"], row["key"])] = row.get("values", {})

    resolved = []
    for pick in picks:
        source = pick.get("source", "anthropic")
        model = pick.get("model", "")
        values = current.get((source, model))
        entry = dict(pick)
        entry["found"] = values is not None
        if values:
            entry["input"] = values.get("input")
            entry["output"] = values.get("output")
            entry["blended"] = blended(values)
            noted = pick.get("blended_when_written")
            now = entry["blended"]
            entry["stale"] = False
            if isinstance(noted, (int, float)) and not isinstance(noted, bool) and noted > 0 and now is not None:
                drift = (now - float(noted)) / float(noted)
                entry["drift_percent"] = drift * 100
                entry["stale"] = abs(drift) > 0.10
        else:
            entry["stale"] = True
        resolved.append(entry)
    return resolved
