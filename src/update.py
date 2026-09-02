"""Entry point: fetch, gate, diff, record, render, notify.

Run order matters. The sanity gate runs before anything is written, so a
broken parse cannot reach the changelog, the snapshots or the site. A source
that fails the gate keeps its previous snapshot untouched and is reported as a
parser problem instead of as news.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import changelog  # noqa: E402
import diffing  # noqa: E402
import notify  # noqa: E402
import render  # noqa: E402
import sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RUN = ROOT / ".run"

SOURCE_ORDER = ["anthropic", "copilot-pricing", "copilot-multipliers", "copilot-deprecations"]


def snapshot_path(source_id: str) -> Path:
    return DATA / f"{source_id}.json"


def load_snapshot(source_id: str) -> dict | None:
    path = snapshot_path(source_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_snapshot(snapshot: dict) -> None:
    path = snapshot_path(snapshot["source"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check vendor pricing and record what changed.")
    parser.add_argument("--cache", action="store_true", help="reuse cached fetches (local dev only)")
    parser.add_argument("--dry-run", action="store_true", help="report but write nothing")
    args = parser.parse_args()

    cache_dir = ROOT / ".cache" if args.cache else None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    all_changes: list[dict] = []
    problems: list[dict] = []
    fresh: list[dict] = []
    snapshots_for_site: list[dict] = []

    for source_id in SOURCE_ORDER:
        previous = load_snapshot(source_id)
        try:
            snapshot = sources.collect(source_id, cache_dir=cache_dir)
        except Exception as exc:  # noqa: BLE001
            # Any failure here (network, HTTP, malformed YAML, schema drift) is
            # a parser problem, not news. Never let it reach the changelog.
            problems.append({"source": source_id, "message": f"fetch or parse failed: {exc}"})
            print(f"{source_id}: FAILED — {exc}")
            if previous:
                snapshots_for_site.append(previous)
            continue

        try:
            diffing.check_sanity(snapshot, previous)
        except diffing.SanityError as exc:
            problems.append({"source": source_id, "message": exc.message})
            print(f"{source_id}: SANITY GATE — {exc.message}")
            # Keep the previous snapshot. It is still correct; the new parse is not.
            if previous:
                snapshots_for_site.append(previous)
            continue

        changes = diffing.diff_snapshots(snapshot, previous)
        first_run = previous is None
        print(
            f"{source_id}: {len(snapshot['rows'])} rows, "
            + ("first run (baseline recorded)" if first_run else f"{len(changes)} changes")
        )
        all_changes.extend(changes)
        fresh.append(snapshot)
        snapshots_for_site.append(snapshot)

    entries = changelog.make_entries(all_changes, today)
    existing = changelog.load(DATA / "changelog.json")
    merged, added = changelog.merge(existing, entries)

    state = {
        "last_checked": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "problems": problems,
        "sources": {s["source"]: len(s["rows"]) for s in snapshots_for_site},
        "changelog_entries": len(merged),
    }

    if args.dry_run:
        print(f"\ndry run: {added} new changelog entries, {len(problems)} problems")
        for entry in entries:
            print("  ", entry["date"], entry["class"], entry["summary"][:90])
        return 0

    for snapshot in fresh:
        save_snapshot(snapshot)
    changelog.save(DATA / "changelog.json", merged)
    (DATA / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    render.write_site(ROOT, merged, snapshots_for_site, state)

    # Hand the workflow everything it needs to notify, without giving this
    # script a GitHub token of its own.
    RUN.mkdir(exist_ok=True)
    notify_needed = bool(entries or problems)
    if notify_needed:
        title = notify.issue_title(entries, problems, today)
        body = notify.issue_body(entries, problems, today)
        (RUN / "issue-title.txt").write_text(title + "\n", encoding="utf-8")
        (RUN / "issue-body.md").write_text(body + "\n", encoding="utf-8")
        try:
            notify.send_email(title, body)
        except Exception as exc:  # noqa: BLE001
            # A mail failure must not lose the run's real work, which is
            # already committed by this point.
            print(f"email: FAILED — {exc}")
    (RUN / "notify.txt").write_text("yes\n" if notify_needed else "no\n", encoding="utf-8")
    (RUN / "problems.txt").write_text("yes\n" if problems else "no\n", encoding="utf-8")

    print(f"\n{added} new changelog entries, {len(problems)} problems, site rebuilt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
