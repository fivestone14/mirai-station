#!/usr/bin/env python3
"""Mirai Viewstation — tiny read-only HTTP server for the tablet view.

Serves the single-page tablet app + a JSON snapshot of live Mirai state, plus an
opt-in raw-data explorer over the on-disk state files. Pure Python stdlib — no third-party deps — but should be run
under the mirai-station venv so snapshot.py can import the skill modules.

    python runtime/viewstation/server.py            # binds 0.0.0.0:8787
    MIRAI_VIEW_PORT=9000 python .../server.py        # custom port

LAN-only by design: no auth, and read-only with ONE deliberate exception —
POST /api/sndk/reasoning flips the SNDK reasoning pause (see _CONTROL_PATH and
do_POST). Nothing else here writes. Don't expose it to the public internet.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")   # the pause stamp reads in market time

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
sys.path.insert(0, str(HERE))
import snapshot as snap  # noqa: E402
import pipeline as pipe  # noqa: E402

PLUGIN_ROOT = snap.PLUGIN_ROOT
STATE_DIR = snap.STATE_DIR
CONFIG_DIR = PLUGIN_ROOT / "runtime" / "watch" / "config"
PORT = int(os.environ.get("MIRAI_VIEW_PORT", "8787"))

# The SNDK reasoning PAUSE — the only file this server writes, and the one path
# the GET and the POST below must agree on. skills/sndk-pro/sndk_read.py reads
# the same file (reasoning_on) and fails open the same way; it is deliberately
# NOT imported here, because the viewstation must stay free of skill imports.
# The contract between the two processes is this file's shape, not a function.
_CONTROL_PATH = STATE_DIR / "sndk_reads" / "control.json"

# directories the raw explorer is allowed to read from (read-only)
RAW_ROOTS = {
    "state": STATE_DIR,
    "config": CONFIG_DIR,
    "skill_logs": PLUGIN_ROOT / "skills" / "mirai-left-eye" / "logs",
}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

# --- snapshot memo (avoid rebuilding for every concurrent poll) ---------------
_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 5.0


def _get_snapshot() -> dict:
    with _lock:
        now = time.monotonic()
        if _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
            return _cache["data"]
        try:
            data = snap.build_snapshot()
        except Exception as exc:  # never 500 the tablet — return an error envelope
            import traceback
            data = {"error": f"{type(exc).__name__}: {exc}",
                    "trace": traceback.format_exc()}
        _cache.update(ts=now, data=data)
        return data


# --- raw explorer -------------------------------------------------------------
def _safe_join(root: Path, rel: str) -> Path | None:
    """Resolve rel under root, refusing anything that escapes it."""
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _raw_index() -> dict:
    out = {}
    for label, root in RAW_ROOTS.items():
        items = []
        if root.exists():
            for p in sorted(root.rglob("*")):
                if p.is_file() and p.suffix in (".json", ".jsonl", ".txt", ".md"):
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    items.append({
                        "rel": str(p.relative_to(root)),
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                        "ext": p.suffix,
                    })
        out[label] = items
    return out


def _raw_file(root_label: str, rel: str, limit: int) -> dict:
    root = RAW_ROOTS.get(root_label)
    if root is None:
        return {"error": "unknown root"}
    path = _safe_join(root, rel)
    if path is None or not path.is_file():
        return {"error": "not found"}
    text = path.read_text(errors="replace")
    if path.suffix == ".jsonl":
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_unparsed": line})
        total = len(rows)
        return {"kind": "jsonl", "total": total, "shown": min(limit, total),
                "rows": rows[-limit:] if limit else rows}
    if path.suffix == ".json":
        try:
            return {"kind": "json", "data": json.loads(text)}
        except json.JSONDecodeError as exc:
            return {"kind": "text", "text": text, "error": str(exc)}
    return {"kind": "text", "text": text[: 200_000]}


# --- sndk reasoning pause -----------------------------------------------------
def _reasoning_state() -> dict:
    """Current pause state. Fails OPEN (reasoning on) to match sndk_read's own
    default — an absent control file means nobody has touched the switch, which
    is not the same as having asked for silence."""
    try:
        d = json.loads(_CONTROL_PATH.read_text())
        if isinstance(d, dict) and isinstance(d.get("reasoning"), bool):
            return d
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"reasoning": True, "since": None, "by": "default"}


# --- request handler ----------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "MiraiViewstation/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet; launchd captures stdout anyway
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        if not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if route in ("/", "/index.html"):
                return self._send_file(STATIC / "index.html")

            if route == "/api/snapshot":
                return self._send_json(_get_snapshot())

            if route == "/api/pipeline":
                try:
                    return self._send_json(pipe.build_pipeline())
                except Exception as exc:  # never 500 the tablet
                    import traceback
                    return self._send_json({"error": f"{type(exc).__name__}: {exc}",
                                            "trace": traceback.format_exc()})

            if route == "/api/spot":
                # the streaming price for the map (cached ~2s in snapshot.live_spot).
                # Fail-open by contract: a null spot means "keep showing the scan price".
                tk = qs.get("ticker", ["SPX"])[0]
                if not re.fullmatch(r"[A-Z.$^]{1,8}", tk):
                    return self._send_json({"error": "bad ticker"}, 400)
                try:
                    return self._send_json(snap.live_spot(tk))
                except Exception as exc:               # never 500 the tablet
                    return self._send_json({"ticker": tk, "spot": None,
                                            "error": f"{type(exc).__name__}: {exc}"})

            if route == "/api/replay":
                day = qs.get("day", [""])[0]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    return self._send_json({"error": "bad day"}, 400)
                try:
                    return self._send_json(snap.replay_day(day))
                except Exception as exc:  # never 500 the tablet
                    import traceback
                    return self._send_json({"error": f"{type(exc).__name__}: {exc}",
                                            "trace": traceback.format_exc()})

            if route == "/api/raw/index":
                return self._send_json(_raw_index())

            if route == "/api/raw/file":
                root = qs.get("root", ["state"])[0]
                rel = unquote(qs.get("path", [""])[0])
                limit = int(qs.get("limit", ["500"])[0])
                return self._send_json(_raw_file(root, rel, limit))

            if route == "/api/health":
                return self._send_json({"ok": True, "port": PORT})

            if route == "/api/sndk/reasoning":
                return self._send_json(_reasoning_state())

            # static assets
            rel = route.lstrip("/")
            asset = _safe_join(STATIC, rel)
            if asset is not None and asset.is_file():
                return self._send_file(asset)

            return self._send_json({"error": "not found", "route": route}, 404)
        except BrokenPipeError:
            pass
        except Exception as exc:
            try:
                self._send_json({"error": str(exc)}, 500)
            except Exception:
                pass

    def do_POST(self):
        """The ONLY write this server accepts, and it stays that way.

        Everything else here is read-only by design, so this is deliberately
        not a general write path: one route, one boolean, one file, and an
        explicit reject for anything else. It flips the SNDK reasoning pause —
        the scanners, the stores and the arrow are untouched by it, so the worst
        a bad request can do is stop or start one sentence being written on a
        beta chart. Note the server binds 0.0.0.0 with no auth, so anyone on the
        LAN can reach this; that blast radius is the reason it is scoped this
        tightly rather than being a generic settings endpoint."""
        parsed = urlparse(self.path)
        if parsed.path != "/api/sndk/reasoning":
            return self._send_json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 512:                      # a boolean needs ~20 bytes
                return self._send_json({"error": "bad body"}, 400)
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(body, dict) or not isinstance(body.get("reasoning"), bool):
                return self._send_json({"error": "expected {\"reasoning\": bool}"}, 400)
            _CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
            state = {"reasoning": body["reasoning"],
                     "since": datetime.now(_ET).isoformat(),
                     "by": "viewstation"}
            tmp = _CONTROL_PATH.with_suffix(".json.tmp")   # atomic: the reader
            tmp.write_text(json.dumps(state))              # must never see a
            tmp.replace(_CONTROL_PATH)                     # torn file
            return self._send_json(state)
        except BrokenPipeError:
            pass
        except Exception as exc:
            try:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 400)
            except Exception:
                pass


def main():
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Mirai Viewstation serving on http://0.0.0.0:{PORT}  (state: {STATE_DIR})",
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("shutting down", flush=True)
        httpd.shutdown()


if __name__ == "__main__":
    main()
