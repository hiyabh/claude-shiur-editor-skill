#!/usr/bin/env bash
#
# Install the shiur-editor skill bundle into ~/.claude/skills/
#
#   curl -L https://hiyabh.github.io/claude-shiur-editor-skill/install.sh | bash
#
# Never overwrites an existing skill of the same name - it is skipped instead.
set -euo pipefail

BUNDLE_URL="https://hiyabh.github.io/claude-shiur-editor-skill/shiur-editor-skill.tar.gz"
SKILLS_DIR="${HOME}/.claude/skills"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "==> downloading the bundle"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$BUNDLE_URL" -o "$TMP/bundle.tar.gz"
elif command -v wget >/dev/null 2>&1; then
  wget -q "$BUNDLE_URL" -O "$TMP/bundle.tar.gz"
else
  echo "ERROR: neither curl nor wget is available." >&2
  exit 1
fi

mkdir -p "$TMP/x"
tar -xzf "$TMP/bundle.tar.gz" -C "$TMP/x"

SRC="$TMP/x/skills"
if [ ! -d "$SRC" ]; then
  SRC="$(find "$TMP/x" -maxdepth 2 -type d -name skills | head -n1)"
fi
if [ ! -d "$SRC" ]; then
  echo "ERROR: could not find a skills/ directory inside the bundle." >&2
  exit 1
fi

mkdir -p "$SKILLS_DIR"
installed=0
skipped=0
for d in "$SRC"/*/; do
  name="$(basename "$d")"
  dest="$SKILLS_DIR/$name"
  if [ -e "$dest" ]; then
    echo "    skip  $name (already exists - not overwritten)"
    skipped=$((skipped + 1))
  else
    cp -r "$d" "$dest"
    echo "    ok    $name"
    installed=$((installed + 1))
  fi
done

echo
echo "==> installed: $installed   skipped: $skipped"
echo "    location: $SKILLS_DIR"
echo
echo "Next:"
echo "  1. Restart Claude Code so it picks up the new skills."
echo "  2. Run the install check:"
echo "       python \"$SKILLS_DIR/shiur-editor/scripts/preflight.py\""
echo "     It reports in Hebrew exactly which packages are missing, if any."
echo
echo "Then just tell Claude:  ערוך לי את השיעור הזה   + a YouTube link or a transcript file."
