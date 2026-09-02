"""Seed the changelog from github/docs history.

GitHub maintains Copilot's pricing tables as YAML in a public repository, so
their change history already exists and is annotated by GitHub themselves. We
do not reconstruct it -- we walk it, diff consecutive revisions of each file,
and emit dated changelog entries carrying the real commit message and link.

The effect is that the site launches with genuine history instead of being
empty for months. Anthropic has no equivalent archive; its record starts at
first run, and the site says so rather than implying otherwise.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import changelog  # noqa: E402
import diffing  # noqa: E402
import sources  # noqa: E402

API = "https://api.github.com"
REPO = "github/docs"
MAX_COMMITS = 300


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _api(url: str) -> list | dict:
    headers = {
        "User-Agent": sources.USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=sources.TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_commits(path: str, max_commits: int = MAX_COMMITS) -> list[dict]:
    """Commits touching *path*, oldest first."""
    commits: list[dict] = []
    page = 1
    while len(commits) < max_commits:
        url = f"{API}/repos/{REPO}/commits?path={urllib.parse.quote(path)}&per_page=100&page={page}"
        batch = _api(url)
        if not isinstance(batch, list) or not batch:
            break
        commits.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    commits = commits[:max_commits]
    commits.reverse()
    return commits


def blob_at(sha: str, path: str) -> str | None:
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{path}"
    try:
        return sources.fetch(url)
    except sources.FetchError:
        return None  # file did not exist at that revision


def backfill_source(source_id: str, verbose: bool = True) -> list[dict]:
    """Walk one Copilot source's history and return changelog entries."""
    spec = sources.SOURCES[source_id]
    path = spec.get("docs_path")
    if not path:
        raise ValueError(f"{source_id} has no github/docs path to walk")

    commits = list_commits(path)
    if verbose:
        print(f"  {source_id}: {len(commits)} commits touching {path}", flush=True)

    entries: list[dict] = []
    previous: dict | None = None

    for commit in commits:
        sha = commit["sha"]
        date = commit["commit"]["committer"]["date"][:10]
        message = commit["commit"]["message"].split("\n")[0].strip()
        text = blob_at(sha, path)
        if text is None:
            continue
        try:
            snapshot = spec["parse"](text)
        except Exception as exc:  # a historical revision may predate the schema
            if verbose:
                print(f"    {date} {sha[:7]}: unparseable ({exc})", flush=True)
            continue

        if previous is not None:
            # The sanity gate is intentionally NOT applied here. A historical
            # revision really can add or drop many rows at once, and we are
            # reading committed fact rather than guessing at a live page.
            changes = diffing.diff_snapshots(snapshot, previous)
            if changes:
                ref = f"https://github.com/{REPO}/commit/{sha}"
                made = changelog.make_entries(changes, date, ref=ref)
                for entry in made:
                    entry["note"] = message
                    # Re-hash so the commit message is part of the identity.
                    entry["id"] = changelog.entry_id(
                        date, entry["source"], entry["class"], entry["key"],
                        entry["summary"] + message,
                    )
                entries.extend(made)
                if verbose:
                    print(f"    {date} {sha[:7]}: {len(changes)} changes — {message[:60]}", flush=True)
        previous = snapshot

    return entries


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    path = root / "data" / "changelog.json"
    existing = changelog.load(path)

    all_entries: list[dict] = []
    for source_id in ("copilot-pricing", "copilot-multipliers", "copilot-deprecations"):
        print(f"backfilling {source_id}…", flush=True)
        all_entries.extend(backfill_source(source_id))

    merged, added = changelog.merge(existing, all_entries)
    changelog.save(path, merged)
    print(f"\nbackfill complete: {added} new entries, {len(merged)} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
