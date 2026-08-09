---
name: claude-usage
description: Refresh and open the local Claude Code usage/cost dashboard. Use when the user asks to see Claude token usage, cost, spend, or a usage dashboard/report, or invokes /claude-usage.
---

# Claude usage dashboard

Runs the local, deterministic usage parser and opens the resulting dashboard. Everything here reads local JSONL session logs only - no network calls, no LLM summarization.

Tool location and commands are platform-dependent:
- macOS: tool dir `~/tools/claude-usage/`, Python is invoked as `python3`, open a file with `open`.
- Windows: tool dir `C:\Tools\claude-usage`, Python is invoked as `python`, open a file with `start`.

Detect the platform from the current session (or ask if unclear) and substitute the right tool dir / command below.

## Steps

1. Run the parser, which first syncs `~/.claude/projects` into the permanent archive at `~/Backups/claude-projects` (copy-only, never deletes - via `rsync` on macOS, `robocopy` on Windows), then parses the archive:
   - macOS: `python3 ~/tools/claude-usage/parse.py`
   - Windows: `python C:\Tools\claude-usage\parse.py`
2. Rebuild the dashboard HTML from the fresh data:
   - macOS: `python3 ~/tools/claude-usage/build_dashboard.py`
   - Windows: `python C:\Tools\claude-usage\build_dashboard.py`
3. Open it:
   - macOS: `open ~/tools/claude-usage/output/dashboard.html`
   - Windows: `start C:\Tools\claude-usage\output\dashboard.html`

Report the total cost and token figures from `parse.py`'s stdout back to the user in a sentence, and mention if it printed any "unknown model" warning to stderr (means `pricing.json` needs a rate added for a new model id).

## Notes

- `pricing.json` in that folder holds per-model USD/MTok rates and is user-editable; some entries (newer model families) are marked as guessed/unconfirmed - don't present cost totals as exact if that warning is present.
- The dashboard is a self-contained local HTML file (embedded data, no fetch/CDN); opening it directly (`open` on macOS, `start` on Windows) is enough, no server needed.
- All generated files (`usage.json`, `usage_daily.csv`, `dashboard.html`) live under the tool dir's `output/`, kept separate from the tool's source so the root folder can be zipped and shared without leaking usage data.
- If the user wants to see it via browser automation (e.g. this Claude Code session controlling Chrome), `file://` URLs aren't reachable by the Chrome extension - serve the folder locally instead, e.g. `python3 -m http.server 8931 --directory ~/tools/claude-usage/output` (macOS) or `python -m http.server 8931 --directory C:\Tools\claude-usage\output` (Windows), navigate to `http://localhost:8931/dashboard.html`, then kill the server afterward.
