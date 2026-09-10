#!/usr/bin/env bash
# Install the llm-price-check agent skill. macOS and Linux.
#
#   ./install.sh                      -> ~/.claude/skills    (Claude Code)
#   ./install.sh --target copilot     -> ~/.copilot/skills   (GitHub Copilot CLI)
#   ./install.sh --target both        -> both of the above
#   ./install.sh .github/skills       -> an explicit root, e.g. to commit with a project
#
# Claude Code and the Copilot CLI read different folders. They are not
# interchangeable: installing for one does not install for the other.
#
# Standalone, no clone needed:
#   curl -sSL https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/install.sh | bash
#   curl -sSL https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/install.sh | bash -s -- --target copilot
#
# Copies the folder rather than symlinking, so it keeps working on a machine
# that has no checkout of this repo — which is the usual case for the machine
# you actually want it on.

set -euo pipefail

SKILL=llm-price-check
RAW=https://raw.githubusercontent.com/zrrbite/llm-price-watch/main/skill/$SKILL
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null)" || HERE=""

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- where to install -------------------------------------------------------
TARGET=claude
DEST_ROOTS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    -h|--help) sed -n '2,18p' "$0" 2>/dev/null || true; exit 0 ;;
    *) DEST_ROOTS+=("$1"); shift ;;   # an explicit root overrides --target
  esac
done

if [ ${#DEST_ROOTS[@]} -eq 0 ]; then
  case "$TARGET" in
    claude)  DEST_ROOTS=("$HOME/.claude/skills") ;;
    copilot) DEST_ROOTS=("$HOME/.copilot/skills") ;;
    both)    DEST_ROOTS=("$HOME/.claude/skills" "$HOME/.copilot/skills") ;;
    *)       die "unknown --target '$TARGET'. Use claude, copilot, or both." ;;
  esac
fi

# --- interpreter ------------------------------------------------------------
# Ask the candidate what it is rather than trusting its name: a `python` on
# PATH is still Python 2 on some machines, and check.py will not run on it.
PY=""
for candidate in python3 python; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if [ "$("$candidate" -c 'import sys; print(sys.version_info[0])' 2>/dev/null)" = "3" ]; then
    PY="$candidate"
    break
  fi
done
[ -n "$PY" ] || die "no working python3 on PATH. The skill needs one to run check.py."

# --- source -----------------------------------------------------------------
# Resolved once, so installing for two agents does not download twice.
TMPDIR_SKILL=""
# The explicit return matters: a trap whose last command fails sets the exit
# status, so a successful install would report failure when there is no tempdir.
cleanup() { [ -n "$TMPDIR_SKILL" ] && rm -rf "$TMPDIR_SKILL"; return 0; }
trap cleanup EXIT

if [ -n "$HERE" ] && [ -f "$HERE/$SKILL/SKILL.md" ]; then
  say "Installing from this checkout."
  SOURCE="$HERE/$SKILL"
else
  say "Downloading from GitHub."
  TMPDIR_SKILL="$(mktemp -d)"
  for file in SKILL.md check.py; do
    curl -fsSL "$RAW/$file" -o "$TMPDIR_SKILL/$file" || die "could not download $file"
  done
  SOURCE="$TMPDIR_SKILL"
fi

# --- install ----------------------------------------------------------------
INSTALLED=()
for root in "${DEST_ROOTS[@]}"; do
  dest="$root/$SKILL"
  mkdir -p "$dest"
  cp "$SOURCE/SKILL.md" "$SOURCE/check.py" "$dest/"
  [ -s "$dest/SKILL.md" ] && [ -s "$dest/check.py" ] || die "install produced empty files in $dest"
  INSTALLED+=("$dest")
done

say ""
for dest in "${INSTALLED[@]}"; do say "Installed to $dest"; done
say "Checking it runs (this hits the network)..."
say ""

DEST="${INSTALLED[0]}"
if "$PY" "$DEST/check.py" "sonnet 5" 2>/dev/null; then
  # Name the agent actually installed for. "Copilot or Claude Code" is what led
  # to files being moved by hand in the first place.
  case "$TARGET" in
    both)    agents="Claude Code and the Copilot CLI" ;;
    copilot) agents="the Copilot CLI" ;;
    *)       agents="Claude Code" ;;
  esac
  [ ${#DEST_ROOTS[@]} -eq 1 ] && [ "${DEST_ROOTS[0]}" != "$HOME/.claude/skills" ] \
    && [ "${DEST_ROOTS[0]}" != "$HOME/.copilot/skills" ] && agents="your agent"
  say ""
  say "Working. Restart $agents, then type /$SKILL."
else
  say ""
  say "The files are in place but the check could not fetch data."
  say "Most likely a proxy or firewall blocking zrrbite.github.io."
  say "The skill still works — it will fall back to fetching brief.txt — but"
  say "test it once in your agent before relying on it."
fi
