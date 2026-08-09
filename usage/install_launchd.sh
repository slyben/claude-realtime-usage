#!/bin/bash
# Installs (or reinstalls) the daily session-archive launchd agent for the
# CURRENT user, from the user-agnostic template in this folder. Safe to
# re-run after editing the template.
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$TOOL_DIR/com.claude-usage.session-archive.plist.template"
LABEL="com.claude-usage.session-archive"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

# Remove the old hardcoded-username agent from an earlier version of this
# tool, if present, so it doesn't run alongside the new one.
OLD_LABEL="com.bertrandcarre.claude-session-archive"
OLD_PLIST="$HOME/Library/LaunchAgents/$OLD_LABEL.plist"
if [ -f "$OLD_PLIST" ]; then
  launchctl unload "$OLD_PLIST" 2>/dev/null || true
  rm -f "$OLD_PLIST"
  echo "Removed legacy agent $OLD_LABEL"
fi

if [ -f "$DEST" ]; then
  launchctl unload "$DEST" 2>/dev/null || true
fi

sed "s|__HOME__|$HOME|g" "$TEMPLATE" > "$DEST"
launchctl load "$DEST"

echo "Installed and loaded $LABEL"
launchctl list | grep "$LABEL" || true
