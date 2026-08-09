#!/bin/bash
# Copies Claude Code session logs to a permanent local archive before the
# ~30-day auto-cleanup in ~/.claude/projects deletes them. Never deletes
# anything at the destination, so it's safe to run repeatedly.
set -euo pipefail

SRC="$HOME/.claude/projects/"
DEST="$HOME/Backups/claude-projects/"
LOG="$HOME/Backups/claude-projects/archive.log"

mkdir -p "$DEST"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  rsync -a --update "$SRC" "$DEST"
  echo "ok, $(find "$DEST" -name '*.jsonl' | wc -l | tr -d ' ') jsonl files in archive"
} >> "$LOG" 2>&1
