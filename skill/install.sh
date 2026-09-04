#!/usr/bin/env bash
# Install the llm-price-check agent skill. macOS and Linux.
#
#   ./install.sh                      -> ~/.claude/skills   (Claude Code and Copilot both read this)
#   ./install.sh ~/.copilot/skills    -> anywhere else you like
#   ./install.sh .github/skills       -> commit it with a project
#
# Standalone, no clone needed:
#   curl -sSL https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/install.sh | bash
#
# Copies the folder rather than symlinking, so it keeps working on a machine
# that has no checkout of this repo — which is the usual case for the machine
# you actually want it on.

set -euo pipefail

SKILL=llm-price-check
RAW=https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/$SKILL
DEST_ROOT="${1:-$HOME/.claude/skills}"
DEST="$DEST_ROOT/$SKILL"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- interpreter ------------------------------------------------------------
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || die "no python3 or python on PATH. The skill needs one to run check.py."

# --- install ----------------------------------------------------------------
mkdir -p "$DEST"

if [ -f "$HERE/$SKILL/SKILL.md" ]; then
  say "Installing from this checkout."
  cp "$HERE/$SKILL/SKILL.md" "$HERE/$SKILL/check.py" "$DEST/"
else
  say "Downloading from GitHub."
  for file in SKILL.md check.py; do
    curl -fsSL "$RAW/$file" -o "$DEST/$file" || die "could not download $file"
  done
fi

# --- verify -----------------------------------------------------------------
[ -s "$DEST/SKILL.md" ] && [ -s "$DEST/check.py" ] || die "install produced empty files"

say ""
say "Installed to $DEST"
say "Checking it runs (this hits the network)..."
say ""

if "$PY" "$DEST/check.py" "sonnet 5" 2>/dev/null; then
  say ""
  say "Working. Restart your editor, then type /$SKILL in Copilot or Claude Code."
else
  say ""
  say "The files are in place but the check could not fetch data."
  say "Most likely a proxy or firewall blocking zrrbite.github.io."
  say "The skill still works — it will fall back to fetching brief.txt — but"
  say "test it once in your agent before relying on it."
fi
