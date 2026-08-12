# claude-usage

A local, deterministic dashboard for Claude Code token usage and cost. No LLM calls, no network access - it only reads the JSONL session transcripts Claude Code already writes to `~/.claude/projects/`, and never sends any of that data anywhere. Runs on macOS and Windows; `parse.py` detects the platform at runtime and dispatches to the matching archive script.

## Why this exists

Claude Code auto-deletes session transcripts after roughly 30 days. This tool:

1. **Archives** those transcripts permanently before they get cleaned up (copy-only, never deletes).
2. **Parses** the archive into token/cost totals per session and per day.
3. **Renders** a self-contained local HTML dashboard from that data.

## Quick start

macOS:
```
python3 parse.py             # sync archive, then parse it -> output/usage.json, output/usage_daily.csv
python3 build_dashboard.py   # embed usage.json into output/dashboard.html
open output/dashboard.html
```

Windows:
```
python parse.py              # sync archive, then parse it -> output/usage.json, output/usage_daily.csv
python build_dashboard.py    # embed usage.json into output/dashboard.html
start output\dashboard.html
```

Or, from Claude Code, just run the `/claude-usage` skill (installed separately, see below) - it does all three steps and reports the totals back to you.

## Installing on a new machine

This folder is self-contained and safe to zip/copy anywhere (it doesn't leak usage data - that only lives in the gitignored `output/` dir, which is generated, and the archive under `~/Backups/claude-projects`, which lives outside the tool dir). No file in it hardcodes an install path: `parse.py`, `build_dashboard.py`, and `install_task_scheduler.ps1` all resolve their own directory at runtime, and `archive_sessions.sh`/`archive_sessions.ps1` only ever touch `~/.claude/projects` and `~/Backups/claude-projects`. Put it wherever you like. The `skill/claude-usage/` folder inside it is a ready-to-copy mirror of the `/claude-usage` slash command; a fresh Claude Code session in this repo can install both the tool and the skill with no other context.

1. **Copy the tool folder** wherever you want it to live - call this `<tool-dir>` below. (Conventionally `~/tools/claude-usage/` on macOS or `C:\Tools\claude-usage` on Windows, but any path works.)

2. **Install the skill** by copying `<tool-dir>/skill/claude-usage/` to Claude Code's user skills directory:
   - macOS: `cp -r <tool-dir>/skill/claude-usage ~/.claude/skills/claude-usage`
   - Windows (PowerShell): `Copy-Item -Recurse "<tool-dir>\skill\claude-usage" "$HOME\.claude\skills\claude-usage"`

   After this, `/claude-usage` works in any Claude Code session on that machine. `skill/claude-usage/SKILL.md` names a specific path for where it expects `<tool-dir>` to be (see the copied file's "Tool location" note) - update that path in the copy at `~/.claude/skills/claude-usage/SKILL.md` if you didn't use the conventional location.

3. **(Optional) Verify pricing.** `pricing.json`'s rates may be stale by the time you install this elsewhere (especially any row noted as an introductory/time-limited price) - check them against https://www.anthropic.com/pricing before trusting cost totals.

4. **(Optional) Install the scheduled archive sync**, so transcripts get backed up even on days you don't run the tool:
   - macOS: `./install_launchd.sh`
   - Windows: `powershell -ExecutionPolicy Bypass -File install_task_scheduler.ps1`

   Skipping this is fine - `parse.py` syncs the archive itself on every run, so as long as you run the tool (or the skill) at least once every ~30 days, nothing gets lost to Claude Code's own cleanup.

5. **Run it** - see Quick start above, or just say "show me Claude usage" / run `/claude-usage` in Claude Code.

## Files

| File | What it is |
|---|---|
| `parse.py` | The parser. Runs the platform's archive script first (`archive_sessions.sh` on macOS, `archive_sessions.ps1` on Windows - picked via `platform.system()`), then walks the archive's `*.jsonl` files, extracts token usage (`assistant` entries) and session titles (`ai-title` entries), prices everything via `pricing.json`, writes `output/usage.json` + `output/usage_daily.csv`. Also writes one `output/sessions/<session_id>.js` file per session - its raw JSONL lines, loaded via `<script src>` when you click into a session in the dashboard (works from `file://`, unlike `fetch`, which browsers block for local files). |
| `archive_sessions.sh` | macOS. `rsync -a --update` from `~/.claude/projects/` to `~/Backups/claude-projects/`. Copy-only - it never deletes anything at the destination, so it's safe to run repeatedly and safe if Claude Code's own cleanup runs in between. |
| `archive_sessions.ps1` | Windows. Same job as the `.sh` version, via `robocopy /E /XO` (no rsync on Windows) - `/XO` skips overwriting a destination file that's already newer or equal, and since `/MIR` is never used, nothing at the destination is ever deleted. |
| `pricing.json` | Editable USD-per-million-token rates per model (input/output/cache-write-5m/cache-write-1h/cache-read). Re-read on every `parse.py` run. Also read by the parent repo's `live_watcher.py` (the single-session live watcher) - it's the one shared pricing file for both tools, so moving/renaming it breaks that too. **Rates for newer model families are marked as guessed/unconfirmed** in the `confidence` field - check `console.anthropic.com` / anthropic.com/pricing and correct this file if you care about exact dollar figures. Unknown model ids get priced at $0 and printed as a warning. |
| `build_dashboard.py` | Embeds `output/usage.json` and `pricing.json` into `dashboard_template.html` (the latter so the dashboard can compute per-turn cost client-side), writes the result to `output/dashboard.html`. |
| `dashboard_template.html` | The dashboard's HTML/CSS/JS source, with `__USAGE_DATA__` and `__PRICING_DATA__` placeholders. Edit this, not `output/dashboard.html` (which gets overwritten every build). |
| `skill/claude-usage/SKILL.md` | Verbatim copy of the `/claude-usage` skill, kept next to the tool so a fresh Claude Code session (or a manual copy) can install it - see "Installing on a new machine" above. Keep this in sync with the live copy at `~/.claude/skills/claude-usage/SKILL.md` if you edit one. |
| `com.claude-usage.session-archive.plist.template` | macOS. User-agnostic launchd agent template (uses a `__HOME__` placeholder, not a real path - don't load this file directly). |
| `install_launchd.sh` | macOS. Substitutes `$HOME` into the plist template and installs/loads it as `~/Library/LaunchAgents/com.claude-usage.session-archive.plist`. Re-run after editing the template. Also removes a legacy hardcoded-username agent from an earlier version of this tool, if present. |
| `install_task_scheduler.ps1` | Windows. Registers a daily Task Scheduler task (`claude-usage-session-archive`) that runs `archive_sessions.ps1` at 03:00, and runs it once immediately. Re-run after editing `archive_sessions.ps1` to pick up changes (it unregisters and recreates the task). |
| `output/` | Generated files only (`usage.json`, `usage_daily.csv`, `dashboard.html`, `sessions/*.js`). Kept out of the root so the rest of this folder can be zipped and shared without leaking your usage data. Safe to delete - `parse.py`/`build_dashboard.py` recreate it. **`dashboard.html` needs `output/sessions/` alongside it** to open individual sessions - if you copy/share just the HTML file on its own, the tiles/chart/table still work but clicking into a session shows "no raw log file found for this session". |

## The archive: `~/Backups/claude-projects/`

Mirrors `~/.claude/projects/`'s own layout: one subfolder per project working directory (with `/` replaced by `-`, e.g. `-Users-you-Development-myproject`), each containing that project's session `.jsonl` files. Not flat - if `ls` at the top level looks sparse, the transcripts are one level down, inside those project folders.

Also holds:
- `archive.log` - one line per sync with a timestamp and file count.
- `launchd.out.log` / `launchd.err.log` - stdout/stderr from the scheduled runs.

This folder is the actual source of truth `parse.py` reads from - it does **not** read `~/.claude/projects/` directly, so archived data survives even after Claude Code deletes the originals.

## Keeping the archive fresh automatically

`parse.py` syncs the archive itself every time it runs, so if you use the dashboard regularly you don't need anything else. The scheduled task is a safety net for days you don't run the tool.

macOS (launchd):
- Installed via `./install_launchd.sh`.
- Runs daily at 03:00, plus once immediately whenever it's loaded (e.g. at login).
- Check it: `launchctl list | grep com.claude-usage.session-archive`
- Uninstall it:
  ```
  launchctl unload ~/Library/LaunchAgents/com.claude-usage.session-archive.plist
  rm ~/Library/LaunchAgents/com.claude-usage.session-archive.plist
  ```

Windows (Task Scheduler):
- Installed via `powershell -ExecutionPolicy Bypass -File install_task_scheduler.ps1`.
- Runs daily at 03:00, plus once immediately when installed.
- Check it: `Get-ScheduledTask -TaskName claude-usage-session-archive`
- Uninstall it:
  ```
  Unregister-ScheduledTask -TaskName claude-usage-session-archive -Confirm:$false
  ```

## The dashboard

`output/dashboard.html` opens directly (`open` on macOS, `start` on Windows), no server required, and the top-level view (tiles/chart/table) is fully self-contained - no `fetch`, no CDN. Clicking into an individual session loads that session's raw JSONL via a `<script src>` pointed at a sibling file in `output/sessions/` (see the `output/` row above for the portability caveat that comes with that).

**Main view:**
- Cost / tokens / session count tiles.
- A stacked bar chart of cost (or tokens) per day, colored by project, with a range selector (7/30/90 days/all-time, defaults to 7 - "all-time" is only as deep as the archive goes, i.e. capped by whenever the scheduled task / this tool started running plus whatever wasn't yet cleaned up).
- A 2-column legend to the right of the chart.
- A sessions table below, sortable by cost, with a toggle to swap the "project" column for the session's `ai-title` (a short title Claude Code itself already generates per session, read locally, never sent anywhere - toggle is off by default since titles can reveal sensitive topics).

Click a session row, or a bar in the day chart (which prompts you to pick a session if several ran that day), to open that session's detail view.

**Session detail view:** renders the session's raw JSONL as a chat transcript instead of a text dump:
- Each turn (user message, or one assistant API call) is a row, folded to a one-line preview by default - click to expand, or use "Expand all"/"Collapse all". Role is shown as a colored dot (green = you, blue = Claude) rather than text.
- Assistant turns show token/cost usage in a packed `time | in | out | cached | $` column layout, right-aligned and aligned across every row like a table.
- Tool calls collapse into a one-line summary (tool name + key input) that expands to show the full input and result.
- Large sessions load in 1MB chunks rather than all at once - it auto-loads more as you scroll near the bottom (or via a manual "Load more" button for big jumps), and shows a "Partial - X% loaded" badge until the whole file is in.
- A mini bar chart at the bottom plots one bar per assistant turn, for tokens-in / tokens-out / tokens-cached / cost (pick one via the dropdown - they're kept as separate scales because cached tokens are typically 10-100x larger than in/out and would otherwise swamp them in a combined chart). A semi-transparent overlay shows/controls which part of the transcript is currently scrolled into view - drag it, or click the chart, to jump. Whichever visible turn has the highest value of the selected metric is highlighted in that metric's color.
- "Copy file path" copies the session's underlying `.jsonl` path(s) to your clipboard - more than one if the session includes subagent transcripts (stored alongside the main file under a `subagents/` folder).

The whole page fits the viewport with no outer scrollbar - only the sessions table / transcript scroll internally, each with a minimum height so they can't get crushed to nothing on a short window.

All dates in the UI use DD-MM(-YYYY) formatting.

## The `/claude-usage` skill

A separate file, `~/.claude/skills/claude-usage/SKILL.md`, wires this up so you can just say "show me Claude usage" or run `/claude-usage` in any Claude Code session, and it'll run the three commands above and open the dashboard for you.
