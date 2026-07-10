#!/usr/bin/env python3
"""Mirai Viewstation — tiny read-only HTTP server for the tablet view.

Serves the single-page tablet app + a JSON snapshot of live Mirai state, plus an
opt-in raw-data explorer over the on-disk state files. Pure Python stdlib — no third-party deps — but should be run
under the mirai-station venv so snapshot.py can import the skill modules.

    python runtime/viewstation/server.py            # binds 0.0.0.0:8787
    MIRAI_VIEW_PORT=9000 python .../server.py        # custom port

LAN-only by design: read-only, no auth, no writes. Don't expose it to the
public internet.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
sys.path.insert(0, str(HERE))
import snapshot as snap  # noqa: E402
import pipeline as pipe  # noqa: E402

PLUGIN_ROOT = snap.PLUGIN_ROOT
STATE_DIR = snap.STATE_DIR
CONFIG_DIR = PLUGIN_ROOT / "runtime" / "watch" / "config"
PORT = int(os.environ.get("MIRAI_VIEW_PORT", "8787"))

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
