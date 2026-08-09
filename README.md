# claude-realtime-usage

Tails one active Claude Code session's `.jsonl` transcript and serves a live chat-transcript view
(per-turn cost/tokens, cold-cache flags, a draggable mini usage chart) in a browser tab, updated
roughly one turn behind the CLI.

Standalone, no external dependencies beyond the Python standard library. `pricing.json` holds
per-model USD/MTok rates; keep it in sync with
[Anthropic's pricing page](https://www.anthropic.com/pricing), especially around pricing changes
(e.g. the sonnet-5 intro price reverting 2026-09-01) - if a model is missing here the live view
shows a persistent "unpriced model" banner rather than a silently wrong number.

**Inherent limitation, not a bug**: a JSONL turn only exists once it's fully written, so this can
never show more than "one turn behind" the CLI.

**Vibe-coded**: this was built almost entirely by Claude Code itself, with human review and a
couple of adversarial LLM review passes rather than exhaustive manual auditing. It's a personal
tool, not a security-hardened product - use at your own risk, and read the code before trusting
it with anything you care about.

## Demo

![Demo](earlydemo.gif)

Recorded from an early build - the "Load full history" button in this clip reorders the
mini-chart when clicked, since fixed. Everything else shown (live tail, cost/token totals,
cold-cache flags) reflects current behavior.

## Usage

The intended way to use this is the `/watch-live` skill (`.claude/skills/watch-live/`), which
starts the server as a background process for *this exact session* and opens it in a Playwright
tab automatically - this requires the Playwright MCP plugin to be enabled in Claude Code. See
that skill for the concrete steps.

To run it manually instead:

```
python live_watcher.py --session <session-uuid>
```

`--session` is required (a bare run without it lists available sessions and exits) - this tool
never guesses which session to watch, so it's safe to run several instances in parallel across
different Claude Code sessions in the same project without ever mixing up which transcript is
shown. It prints the local URL to open (`http://127.0.0.1:<port>/t/<token>/`); the port is
OS-assigned by default (`--port 0`) to avoid clashes between concurrently-watched sessions, and
the URL includes a random token since the server has no other auth.

The watcher is a plain foreground process when run manually - `Ctrl+C` to stop it. When launched
via the skill's `run_in_background`, it's a child of that session's shell and exits automatically
when the Claude Code session does.

## Files

- `live_watcher.py` - stdlib-only Python server (no dependencies to install).
- `live_watcher_template.html` - the browser-side transcript/chart renderer.
- `pricing.json` - per-model USD/MTok rates, own copy (see note above).
- `.claude/skills/watch-live/` - the `/watch-live` skill.

Both the server and the `/watch-live` skill are cross-platform (macOS/Linux/Windows) - the skill
branches its Python invocation (`python3` vs `python`) and stop fallback (`kill` vs `taskkill`) by
detected OS.

## License

MIT - see [LICENSE](LICENSE).
