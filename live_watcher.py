#!/usr/bin/env python3
# Copyright (c) 2026 Bertrand Carre
# Licensed under the MIT License - see LICENSE in the project root.
"""Live watcher: tails one Claude Code session's JSONL and serves the same
chat-transcript view as claude-usage's static dashboard, updated ~1 turn behind
the CLI. Cross-platform (stdlib only, no third-party dependencies); the
accompanying /watch-live skill branches its shell commands by OS too.

Usage:
    python live_watcher.py --session <uuid> [--port N]

If --session is omitted, lists available sessions under ~/.claude/projects and
exits - this tool never guesses which session to watch (see README).
"""
import argparse
import atexit
import glob
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = TOOL_DIR / "live_watcher_template.html"
PRICING_PATH = TOOL_DIR / "pricing.json"
LOCK_DIR = Path(tempfile.gettempdir()) / "live_watcher"

UUID_RE = re.compile(r"^[0-9a-f-]{36}$", re.IGNORECASE)
AGENT_ID_RE = re.compile(r"^[0-9a-f]{17}$")


def read_project_folder(jsonl_path):
    # Last two path components of the session's own recorded `cwd` (e.g.
    # "Claude/claude-realtime-usage"), for the browser tab title - more informative than one
    # segment alone when projects are nested under a shared parent like ~/Development/Code/Claude.
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                cwd = d.get("cwd")
                if cwd:
                    # Split on both separators rather than pathlib.Path(cwd).parts, which
                    # parses using the *running* platform's rules - the watcher and the
                    # session it watches are always the same machine/OS in practice, but this
                    # avoids relying on that instead of just handling both explicitly.
                    parts = [p for p in re.split(r"[/\\]+", cwd) if p]
                    return "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else None)
    except OSError:
        pass
    return None


def subagents_dir(jsonl_path):
    # Subagent transcripts live under <project-dir>/<session-uuid>/subagents/ - a
    # subdirectory named after the session (jsonl_path's own stem), sibling to the
    # <uuid>.jsonl file itself, NOT directly in the project dir.
    return jsonl_path.parent / jsonl_path.stem / "subagents"

# --- session resolution -----------------------------------------------------

def find_session_jsonl(session_id):
    projects_dir = Path.home() / ".claude" / "projects"
    matches = glob.glob(str(projects_dir / "*" / f"{session_id}.jsonl"))
    return Path(matches[0]) if matches else None


def list_available_sessions():
    projects_dir = Path.home() / ".claude" / "projects"
    paths = sorted(glob.glob(str(projects_dir / "*" / "*.jsonl")), key=os.path.getmtime, reverse=True)
    print("No --session given. Available sessions (newest first):", file=sys.stderr)
    for p in paths[:30]:
        p = Path(p)
        print(f"  {p.stem}  ({p.parent.name})", file=sys.stderr)


def wait_for_session_file(session_id, timeout=30):
    deadline = time.time() + timeout
    path = find_session_jsonl(session_id)
    while path is None and time.time() < deadline:
        time.sleep(1)
        path = find_session_jsonl(session_id)
    return path


# --- lockfile ------------------------------------------------------------------
#
# Two files per session, not one:
#   <uuid>.lock  - empty sentinel, existence is the mutual-exclusion primitive
#                  (claimed via O_CREAT|O_EXCL - exactly one launcher can win it).
#   <uuid>.json  - the actual info (pid/port/token/url/...), written by renaming a
#                  temp file into place, so it only ever exists fully-formed. A
#                  reader (the /watch-live skill) polling for the .json file can
#                  never observe a truncated/empty read, which a direct
#                  O_CREAT|O_EXCL write to the info file itself could produce.
# release_lockfile only deletes either file after confirming the info file's
# `token` still matches this process's own token, so a launcher that wrongly
# decided a live instance's lockfile was stale (e.g. its /health probe just
# timed out under load) can't have its cleanup delete a *different*, legitimate
# instance's lockfile out from under it.

def mutex_path(session_id):
    return LOCK_DIR / f"{session_id}.lock"


def info_path(session_id):
    return LOCK_DIR / f"{session_id}.json"


def probe_health(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _write_info_atomic(path, info):
    tmp = path.parent / f"{path.name}.tmp{os.getpid()}"
    fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.replace(str(tmp), str(path))


def acquire_lockfile(session_id, info):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    mpath = mutex_path(session_id)
    ipath = info_path(session_id)

    def try_claim():
        fd = os.open(str(mpath), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)

    try:
        try_claim()
    except FileExistsError:
        existing = None
        try:
            existing = json.loads(ipath.read_text(encoding="utf-8"))
        except Exception:
            pass
        health = probe_health(existing["url"]) if existing and "url" in existing else None
        if health and health.get("session_id") == session_id:
            print(f"Already watching this session at {existing['url']}", file=sys.stderr)
            sys.exit(3)
        # stale mutex (process gone, or a previous launch died between claiming the
        # mutex and writing the info file) - clear both and retry once.
        for p in (mpath, ipath):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        try:
            try_claim()
        except FileExistsError:
            print("Another watcher just claimed this session; try again.", file=sys.stderr)
            sys.exit(3)

    _write_info_atomic(ipath, info)
    return mpath, ipath


def release_lockfile(mpath, ipath, token):
    try:
        existing = json.loads(ipath.read_text(encoding="utf-8"))
        if existing.get("token") != token:
            return  # not ours (anymore) - don't touch it
    except Exception:
        pass  # unreadable/missing info file - fall through and clean up regardless
    for p in (mpath, ipath):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# --- HTTP handler --------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # quiet - this runs as a background process

    def _bad_host(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host not in ("localhost", "127.0.0.1")

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/health":
            if self._bad_host():
                return self.send_error(403)
            return self._send_json(200, {
                "session_id": self.server.session_id,
                "pid": os.getpid(),
            })

        m = re.match(r"^/t/([0-9a-f]{32})(/.*)?$", parsed.path)
        if not m or m.group(1) != self.server.token:
            return self.send_error(404)
        if self._bad_host():
            return self.send_error(403)

        sub = m.group(2)
        if sub is None:
            # no trailing slash: redirect so the browser's own document URL ends up
            # with one before any relative fetch('initial'/'events'/...) can run -
            # serving the page directly here would leave the client resolving those
            # relative URLs against the wrong base and 404ing on every one of them.
            self.send_response(301)
            self.send_header("Location", f"/t/{self.server.token}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        m2 = re.match(r"^/agent/([0-9a-f]{17})(/.*)?$", sub)
        if m2:
            return self._dispatch_agent(m2.group(1), m2.group(2), qs)

        if sub == "/":
            return self._handle_page()
        if sub == "/initial":
            return self._handle_initial(qs)
        if sub == "/history":
            return self._handle_history(qs)
        if sub == "/events":
            return self._handle_events(qs)
        if sub == "/agents":
            return self._handle_agents()
        self.send_error(404)

    def _dispatch_agent(self, agent_id, agent_sub, qs):
        # AGENT_ID_RE already constrained agent_id via the do_GET regex above; re-check
        # here too since this method could in principle be called from elsewhere later.
        # Because the id can never contain "/" or ".." once it matches [0-9a-f]{17}, the
        # f-string below can never escape the subagents/ directory - no further sanitizing
        # is needed, but the resolved-prefix check is kept anyway as defense-in-depth.
        if not AGENT_ID_RE.match(agent_id):
            return self.send_error(404)
        subdir = subagents_dir(self.server.jsonl_path)
        candidate = subdir / f"agent-{agent_id}.jsonl"
        try:
            resolved = candidate.resolve()
            if not str(resolved).startswith(str(subdir.resolve()) + os.sep):
                return self.send_error(404)
        except OSError:
            return self.send_error(404)
        if not candidate.is_file():
            return self.send_error(404)

        if agent_sub is None:
            self.send_response(301)
            self.send_header("Location", f"/t/{self.server.token}/agent/{agent_id}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if agent_sub == "/":
            return self._handle_page(jsonl_path=candidate, agent_id=agent_id)
        if agent_sub == "/initial":
            return self._handle_initial(qs, jsonl_path=candidate)
        if agent_sub == "/history":
            return self._handle_history(qs, jsonl_path=candidate)
        if agent_sub == "/events":
            return self._handle_events(qs, jsonl_path=candidate)
        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        m = re.match(r"^/t/([0-9a-f]{32})/shutdown$", parsed.path)
        if not m or m.group(1) != self.server.token:
            return self.send_error(404)
        if self._bad_host():
            return self.send_error(403)
        self._send_json(200, {"ok": True})
        threading.Thread(target=self.server.request_shutdown, daemon=True).start()

    def _handle_page(self, jsonl_path=None, agent_id=None):
        html = TEMPLATE_PATH.read_text(encoding="utf-8")
        html = html.replace("__PRICING_DATA__", self.server.pricing_json)
        session_label = self.server.session_id
        if agent_id:
            session_label = f"{session_label} · subagent {agent_id[:8]}"
        html = html.replace("__SESSION_ID__", session_label)
        html = html.replace("__SESSION_PATH__", str(jsonl_path or self.server.jsonl_path))
        html = html.replace("__IS_SUBAGENT__", "true" if agent_id else "false")
        html = html.replace("__PROJECT_FOLDER__", self.server.project_folder)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _handle_initial(self, qs, jsonl_path=None):
        tail_bytes = int(qs.get("tail", ["200000"])[0])
        path = jsonl_path or self.server.jsonl_path
        approx_size = os.path.getsize(path)
        start = max(0, approx_size - tail_bytes)
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
        truncated = start > 0
        if truncated:
            nl = data.find(b"\n")
            if nl != -1:
                start += nl + 1
                data = data[nl + 1:]
            # else: no newline in the whole tail window (one huge line) - can't align
            # the start any better; the end-alignment below still protects it.
        # Newline-align the END too, mirroring _handle_events: a page load that lands
        # mid-write of the file's last line must not include that partial line, or it
        # silently fails JSON.parse and is lost until a later SSE poll re-reads past
        # it under a since= that already skipped it. `size` reported to the client is
        # derived from what was actually read and aligned, not from `approx_size`.
        nl_end = data.rfind(b"\n")
        if nl_end != -1:
            data = data[:nl_end + 1]
        else:
            data = b""  # whole window is one still-being-written line; show nothing yet
        size = start + len(data)
        text = data.decode("utf-8", errors="replace")
        self._send_json(200, {"text": text, "size": size, "truncated": truncated, "start": start})

    def _handle_history(self, qs, jsonl_path=None):
        before = int(qs.get("before", ["0"])[0])
        with open(jsonl_path or self.server.jsonl_path, "rb") as f:
            data = f.read(before)
        self._send_json(200, {"text": data.decode("utf-8", errors="replace")})

    def _handle_events(self, qs, jsonl_path=None):
        since = int(qs.get("since", ["0"])[0])
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # Close-delimited on purpose: this response never ends within a normal
            # request/response cycle (it streams until the client disconnects or the
            # server shuts down), so there's no meaningful Content-Length or chunked
            # framing to offer. Advertising keep-alive without either would be an
            # actual protocol violation and leave the handler thread blocked reading
            # a next request that will never come once the loop below returns.
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            last_heartbeat = time.time()
            path = jsonl_path or self.server.jsonl_path
            while not self.server.shutting_down:
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0

                if size < since:
                    # file shrank or was replaced (truncation/rotation) - tell the client
                    # to reload from scratch rather than freezing forever waiting for
                    # growth past an offset that no longer exists.
                    self.wfile.write(b"event: reset\ndata: {}\n\n")
                    self.wfile.flush()
                    since = 0
                    time.sleep(1)
                    continue

                if size > since:
                    with open(path, "rb") as f:
                        f.seek(since)
                        chunk = f.read(size - since)
                    nl = chunk.rfind(b"\n")
                    if nl == -1:
                        # mid-write of a single large line; wait for it to finish rather
                        # than emitting a partial JSON line that would fail to parse.
                        time.sleep(1)
                        continue
                    aligned = chunk[:nl + 1]
                    since += len(aligned)
                    text = aligned.decode("utf-8", errors="replace")
                    payload = json.dumps({"text": text, "size": since})
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_heartbeat = time.time()
                else:
                    if time.time() - last_heartbeat > 12:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_heartbeat = time.time()
                    time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def _handle_agents(self):
        subdir = subagents_dir(self.server.jsonl_path)
        agents = []
        if subdir.is_dir():
            for meta_path in sorted(subdir.glob("agent-*.meta.json")):
                # meta_path.stem strips only the trailing ".json", leaving "agent-<id>.meta" -
                # strip both the "agent-" prefix and ".meta" suffix explicitly.
                stem = meta_path.stem
                if not stem.startswith("agent-") or not stem.endswith(".meta"):
                    continue
                agent_id = stem[len("agent-"):-len(".meta")]
                if not AGENT_ID_RE.match(agent_id):
                    continue
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue  # mid-write - will show up on a later poll
                # v1 scope: only direct (depth 1) subagents of the main session. A subagent
                # spawning its own subagent isn't surfaced yet (see README).
                if meta.get("spawnDepth") != 1:
                    continue
                jsonl_path = subdir / f"agent-{agent_id}.jsonl"
                agents.append({
                    "id": agent_id,
                    "agentType": meta.get("agentType"),
                    "description": meta.get("description"),
                    "toolUseId": meta.get("toolUseId"),
                    "spawnDepth": meta.get("spawnDepth"),
                    "model": meta.get("model"),
                    "hasTranscript": jsonl_path.is_file(),
                })
        self._send_json(200, {"agents": agents})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


# --- lifecycle -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session", help="session UUID to watch (required)")
    ap.add_argument("--port", type=int, default=0, help="port to bind (default: OS-assigned ephemeral port)")
    args = ap.parse_args()

    if not args.session:
        list_available_sessions()
        sys.exit(1)
    session_id = args.session
    if not UUID_RE.match(session_id):
        print(f"--session doesn't look like a UUID: {session_id!r}", file=sys.stderr)
        sys.exit(1)

    print(f"Resolving session {session_id}...", flush=True)
    jsonl_path = find_session_jsonl(session_id)
    if jsonl_path is None:
        print("Session file not found yet, waiting up to 30s for it to appear...", flush=True)
        jsonl_path = wait_for_session_file(session_id)
    if jsonl_path is None:
        print(f"No session file found for {session_id} under ~/.claude/projects/*/. Giving up.", file=sys.stderr)
        sys.exit(2)
    print(f"Watching {jsonl_path}", flush=True)

    pricing = json.loads(PRICING_PATH.read_text(encoding="utf-8"))["models"]
    token = secrets.token_hex(16)

    server = Server(("127.0.0.1", args.port), Handler)
    server.session_id = session_id
    server.jsonl_path = jsonl_path
    server.project_folder = read_project_folder(jsonl_path) or session_id[:8]
    server.token = token
    server.pricing_json = json.dumps(pricing)
    server.shutting_down = False

    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/t/{token}/"

    lock_info = {
        "pid": os.getpid(),
        "port": actual_port,
        "token": token,
        "url": url,
        "jsonl_path": str(jsonl_path),
        "session_id": session_id,
        "started_at": time.time(),
    }
    mpath, ipath = acquire_lockfile(session_id, lock_info)

    def cleanup():
        release_lockfile(mpath, ipath, token)

    atexit.register(cleanup)

    def request_shutdown():
        server.shutting_down = True
        server.shutdown()

    server.request_shutdown = request_shutdown

    print(f"Serving at {url}", flush=True)
    print(f"Lockfile: {ipath}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutting_down = True
        server.server_close()
        cleanup()


if __name__ == "__main__":
    main()
