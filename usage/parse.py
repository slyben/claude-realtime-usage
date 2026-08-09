#!/usr/bin/env python3
"""Deterministic parser for Claude Code local session logs.

Before parsing, runs archive_sessions.sh to sync ~/.claude/projects into the
permanent archive at ~/Backups/claude-projects (copy-only, never deletes),
then parses THAT archive rather than the live, auto-cleaned-up directory.
This means a run of this tool is always what keeps the archive fresh -
the daily launchd job is just a safety net for days the tool isn't run.

Extracts token usage from "assistant" entries and session titles from
"ai-title" entries, computes cost from pricing.json, and writes into
output/ (kept out of the tool's root so the root can be zipped/shared
without generated data):
  - output/usage.json       full detail (sessions + daily aggregates)
  - output/usage_daily.csv  date,project,cost,input,output,cache_write,cache_read
  - output/dashboard.html   self-contained local viewer (no network calls)

No LLM calls, no network access. Everything is a straight read of on-disk
JSONL files that Claude Code (or this tool's own archive step) already wrote.
"""
import json
import glob
import csv
import os
import shutil
import sys
import platform
import subprocess
from collections import defaultdict
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
ARCHIVE_DIR = os.path.join(HOME, "Backups", "claude-projects")
PROJECTS_DIR = ARCHIVE_DIR
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(TOOL_DIR, "output")
SESSIONS_DIR = os.path.join(OUTPUT_DIR, "sessions")
PRICING_PATH = os.path.join(TOOL_DIR, "pricing.json")
ARCHIVE_SCRIPT_MAC = os.path.join(TOOL_DIR, "archive_sessions.sh")
ARCHIVE_SCRIPT_WIN = os.path.join(TOOL_DIR, "archive_sessions.ps1")


def sync_archive():
    if platform.system() == "Windows":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ARCHIVE_SCRIPT_WIN]
    else:
        cmd = ["bash", ARCHIVE_SCRIPT_MAC]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"warning: archive sync failed, parsing existing archive as-is: {result.stderr}", file=sys.stderr)

UNKNOWN_MODEL_RATE = {"input": 0, "output": 0, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0}


def load_pricing():
    with open(PRICING_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["models"]


def cwd_to_project(cwd):
    if not cwd:
        return "(unknown)"
    return os.path.basename(cwd.rstrip("/\\")) or cwd


def safe_filename(key):
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in key)


def cost_for_usage(usage, rates):
    cache_creation = usage.get("cache_creation") or {}
    write_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
    write_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
    # some log lines only have the flat total, not the 5m/1h split
    if not cache_creation and usage.get("cache_creation_input_tokens"):
        write_5m = usage["cache_creation_input_tokens"]

    input_tok = usage.get("input_tokens", 0)
    output_tok = usage.get("output_tokens", 0)
    read_tok = usage.get("cache_read_input_tokens", 0)

    cost = (
        input_tok * rates["input"]
        + output_tok * rates["output"]
        + write_5m * rates["cache_write_5m"]
        + write_1h * rates["cache_write_1h"]
        + read_tok * rates["cache_read"]
    ) / 1_000_000.0

    return cost, {
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "cache_write_tokens": write_5m + write_1h,
        "cache_read_tokens": read_tok,
    }


def main():
    sync_archive()
    pricing = load_pricing()
    unknown_models = set()

    # session_id -> accumulated info
    sessions = {}
    # (date, project) -> aggregated token/cost totals
    daily = defaultdict(lambda: {
        "cost": 0.0, "input_tokens": 0, "output_tokens": 0,
        "cache_write_tokens": 0, "cache_read_tokens": 0, "messages": 0,
    })

    # session_id (or fallback path key) -> list of (timestamp, path, raw_line),
    # kept for the "view raw session content" feature in the dashboard.
    raw_entries = defaultdict(list)

    files = sorted(glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True))
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    dtype = d.get("type")
                    sid = d.get("sessionId") or d.get("session_id")
                    raw_entries[sid or path].append((d.get("timestamp") or "", path, line))

                    if dtype == "ai-title" and sid:
                        s = sessions.setdefault(sid, _new_session(sid))
                        s["ai_title"] = d.get("aiTitle") or s["ai_title"]
                        continue

                    if dtype != "assistant":
                        continue

                    msg = d.get("message") or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue

                    model = msg.get("model") or "(unknown)"
                    rates = pricing.get(model)
                    if rates is None:
                        rates = UNKNOWN_MODEL_RATE
                        unknown_models.add(model)

                    ts = d.get("timestamp")
                    cwd = d.get("cwd")
                    project = cwd_to_project(cwd)

                    s = sessions.setdefault(sid or path, _new_session(sid or path))
                    s["project"] = project
                    s["cwd"] = cwd or s["cwd"]
                    if ts:
                        if not s["first_ts"] or ts < s["first_ts"]:
                            s["first_ts"] = ts
                        if not s["last_ts"] or ts > s["last_ts"]:
                            s["last_ts"] = ts

                    cost, toks = cost_for_usage(usage, rates)
                    s["cost"] += cost
                    s["input_tokens"] += toks["input_tokens"]
                    s["output_tokens"] += toks["output_tokens"]
                    s["cache_write_tokens"] += toks["cache_write_tokens"]
                    s["cache_read_tokens"] += toks["cache_read_tokens"]
                    s["messages"] += 1
                    s["models"].add(model)

                    if ts:
                        date = ts[:10]  # YYYY-MM-DD, UTC as logged
                        key = (date, project)
                        agg = daily[key]
                        agg["cost"] += cost
                        agg["input_tokens"] += toks["input_tokens"]
                        agg["output_tokens"] += toks["output_tokens"]
                        agg["cache_write_tokens"] += toks["cache_write_tokens"]
                        agg["cache_read_tokens"] += toks["cache_read_tokens"]
                        agg["messages"] += 1
        except OSError as e:
            print(f"warning: could not read {path}: {e}", file=sys.stderr)

    session_list = []
    for s in sessions.values():
        if s["messages"] == 0:
            continue
        s["models"] = sorted(s["models"])
        session_list.append(s)
    session_list.sort(key=lambda s: s["first_ts"] or "")

    daily_list = [
        {"date": date, "project": project, **totals}
        for (date, project), totals in sorted(daily.items())
    ]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "unknown_models": sorted(unknown_models),
        "sessions": session_list,
        "daily": daily_list,
        "totals": {
            "cost": sum(s["cost"] for s in session_list),
            "input_tokens": sum(s["input_tokens"] for s in session_list),
            "output_tokens": sum(s["output_tokens"] for s in session_list),
            "cache_write_tokens": sum(s["cache_write_tokens"] for s in session_list),
            "cache_read_tokens": sum(s["cache_read_tokens"] for s in session_list),
        },
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    write_session_raw_files(session_list, raw_entries)

    with open(os.path.join(OUTPUT_DIR, "usage.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    with open(os.path.join(OUTPUT_DIR, "usage_daily.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "project", "cost_usd", "input_tokens", "output_tokens",
                    "cache_write_tokens", "cache_read_tokens", "messages"])
        for row in daily_list:
            w.writerow([row["date"], row["project"], f"{row['cost']:.4f}",
                        row["input_tokens"], row["output_tokens"],
                        row["cache_write_tokens"], row["cache_read_tokens"], row["messages"]])

    print(f"Parsed {len(files)} files, {len(session_list)} sessions.")
    print(f"Total cost (per pricing.json): ${out['totals']['cost']:.2f}")
    if unknown_models:
        print(f"Unknown models (priced at $0, add to pricing.json): {sorted(unknown_models)}", file=sys.stderr)
    print(f"Wrote {OUTPUT_DIR}/usage.json and usage_daily.csv")


def write_session_raw_files(session_list, raw_entries):
    """Writes two files per session holding its raw JSONL lines: a .js file the dashboard
    loads via <script src> (works from file://, unlike fetch/XHR which browsers block for
    local files) and a plain .txt sibling with the same text, unwrapped - for a server-backed
    embedder (e.g. claude-realtime-usage's live_watcher.py) to fetch() directly instead,
    since that's real http:// and doesn't need the script-tag workaround."""
    if os.path.isdir(SESSIONS_DIR):
        shutil.rmtree(SESSIONS_DIR)
    os.makedirs(SESSIONS_DIR, exist_ok=True)

    for s in session_list:
        sid = s["session_id"]
        entries = raw_entries.get(sid, [])
        entries.sort(key=lambda e: (e[0], e[1]))
        raw_text = "\n".join(e[2] for e in entries)
        base = os.path.join(SESSIONS_DIR, safe_filename(sid))
        js = "__recvSession(" + json.dumps(sid) + "," + json.dumps(raw_text) + ");"
        with open(base + ".js", "w", encoding="utf-8") as f:
            f.write(js)
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(raw_text)
        s["source_paths"] = sorted({path for _, path, _ in entries})


def _new_session(sid):
    return {
        "session_id": sid, "project": "(unknown)", "cwd": None, "ai_title": None,
        "first_ts": None, "last_ts": None, "cost": 0.0,
        "input_tokens": 0, "output_tokens": 0, "cache_write_tokens": 0,
        "cache_read_tokens": 0, "messages": 0, "models": set(),
    }


if __name__ == "__main__":
    main()
