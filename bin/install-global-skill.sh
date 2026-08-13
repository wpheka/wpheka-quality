#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HOME}/.agents/skills/wpheka-quality"

mkdir -p "$DEST"
cp "$ROOT/skills/wpheka-quality/SKILL.md" "$DEST/SKILL.md"

echo "Installed:"
echo "  $DEST/SKILL.md"
echo
echo "The Skill is review-only. Review the file before granting an agent unattended shell execution."
