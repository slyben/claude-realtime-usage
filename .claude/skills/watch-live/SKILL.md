---
name: watch-live
description: Start or stop a live watcher for this Claude Code session's transcript (cost/tokens/cold-cache updating ~1 turn behind the CLI). Use when the user asks to watch this session live, invokes /watch-live, or asks to stop the live watcher.
---

# Live session watcher

Starts `live_watcher.py` (a small local server in this repo) as a background process tailing
*this exact session's* JSONL file, and gives you a URL to open in your own browser. The steps
below assume this repo (wherever it's checked out) is the current project.

Default mode just prints the URL - open it yourself in whatever browser you normally use, no
extra dependency needed. `/watch-live auto` instead opens it for you in a Playwright-controlled
tab, if the Playwright MCP plugin is connected.

`live_watcher.py` itself is cross-platform (stdlib only). Only this skill's own shell commands
differ slightly by OS - see Platform below.

Never guesses which session to watch - it always targets this session's own UUID, so it's safe
to run in multiple Claude Code sessions in the same project at once without ever showing the
wrong transcript.

## Platform

Detect the platform from the current session (or ask if unclear) and substitute accordingly:

- macOS/Linux: Python is invoked as `python3`; the stop fallback (if `/shutdown` doesn't
  respond) is `kill -9 <pid>`.
- Windows: Python is invoked as `python`; the stop fallback is `taskkill /PID <pid> /F`.

Set `$PY` to the right command for the commands below.

## Steps

Arguments: bare `/watch-live` starts (or reattaches to) the watcher and just prints the URL;
`/watch-live auto` does the same but also opens it in a Playwright-controlled tab;
`/watch-live stop` stops it.

### 1. Get this session's own UUID and the lockfile path

The UUID is the directory name in this session's own scratchpad path, already visible in your
system context (the "Scratchpad Directory" section) - same structure on every OS, just with
backslashes on Windows and forward slashes elsewhere:
`.../.claude/tmp/claude/<project>/<SESSION-UUID>/scratchpad`

Extract `<SESSION-UUID>`. If for some reason it isn't visible in context, fall back to the
newest-modified `.jsonl` under this project's own `~/.claude/projects/<project>/` directory.

Set `$UUID` to that value. Then resolve `$LOCK` by asking Python for its own temp directory
(`tempfile.gettempdir()`), rather than assuming a `$TEMP`/`$TMPDIR` env var is set correctly -
this is exactly how `live_watcher.py` resolves the same path internally, on every OS, so the two
are guaranteed to agree:

```
LOCK=$($PY -c "import tempfile,os,sys; print(os.path.join(tempfile.gettempdir(),'live_watcher',sys.argv[1]+'.json'))" "$UUID")
```

### 2. Check for an already-running watcher for this session

```
if [ -f "$LOCK" ]; then
  URL=$($PY -c "import json,sys; print(json.load(open(sys.argv[1]))['url'])" "$LOCK" 2>/dev/null)
  if [ -n "$URL" ]; then
    curl -s --max-time 2 "${URL%/t/*/}/health" | grep -q "\"session_id\": \"$UUID\"" && echo "ALIVE:$URL"
  fi
fi
```

If that printed `ALIVE:<url>`, skip straight to step 4 with that URL (for `/watch-live` or
`/watch-live auto`) or step 5 (for `/watch-live stop`) - don't start a second server.

### 3. Start (`/watch-live` or `/watch-live auto`, no existing watcher found)

```
cd <this repo's root - the directory containing this .claude/skills/watch-live/ folder>
$PY -u live_watcher.py --session $UUID
```

Run this via the Bash tool with `run_in_background: true`. Then poll for the lockfile with a
literal bounded wait loop (Claude has no sub-second timer, so this must be an actual shell loop,
not repeated tool calls):

```
for i in $(seq 1 40); do [ -f "$LOCK" ] && break; sleep 0.25; done
cat "$LOCK"
```

Read `url` from the printed JSON. If the lockfile never appeared, check the background process's
captured output (`BashOutput`) for the reason - most likely a port bind failure or the session
file not existing yet - and report it to the user instead of hanging silently.

### 4. Report the URL (`/watch-live` or `/watch-live auto`, resolved via step 2 or 3)

Always report the URL back to the user in chat - it stays valid as long as the watcher process is
running, so they can (re)open it manually at any time regardless of which mode was used.

- Bare `/watch-live`: that's it, stop here. Don't open anything yourself - the user opens the URL
  in their own browser.
- `/watch-live auto`: additionally open it yourself, in a Playwright-controlled tab. If the
  Playwright MCP tools (`mcp__playwright__browser_navigate` or equivalent) aren't available -
  deferred-but-unloadable, or the plugin isn't connected at all - say so plainly and fall back to
  the bare-mode behavior (URL only) rather than failing the whole command.

### 5. Stop (`/watch-live stop`)

Resolve the lockfile/URL as in step 2. If found and alive:

```
curl -s -X POST --max-time 2 "${URL%/}/shutdown"
```

(`$URL` already ends in `/` - trimming it before appending `/shutdown` avoids a double slash
that the server's route matching would 404 on.)

Give it a moment, then confirm the lockfile is gone (`[ -f "$LOCK" ]` should now fail). If
`/shutdown` doesn't respond within a couple seconds but `/health` still confirms it's the right
process, fall back to the platform's kill command from step 0 (`kill -9 <pid>` on macOS/Linux,
`taskkill /PID <pid> /F` on Windows) using the `pid` from the lockfile JSON - never kill a pid
without first confirming via `/health` that it's actually this watcher (a stale lockfile could
contain a pid that's since been reused by an unrelated process). Confirm to the user that it
stopped; skip the browser step.

## Notes

- The watcher is a child of this session's background shell and exits when this CLI session
  exits - nothing to clean up if you just close the terminal. Running `/watch-live stop`
  explicitly is only needed if you want to stop it while the session is still open.
- It's bound to `127.0.0.1` only and every URL includes a random token, so it's not reachable
  from the network and can't be guessed by another local page.
- Subagent (Task/Agent-tool) transcripts show up nested inside their spawning tool call once
  expanded, each with its own "open in new tab" link - this skill only ever needs to open the
  main session's URL; it doesn't need to discover or open subagent tabs itself.
- If the sonnet-5 intro pricing (or any model's rate) has changed, update `pricing.json` in this
  repo directly (against https://www.anthropic.com/pricing). If you also use the sibling
  `claude-usage` dashboard tool locally, its `pricing.json` is a separate copy by design (this
  tool has no runtime dependency on that repo) - re-sync from there instead, so they can drift.
