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

"LAN-only" is enforced, not merely assumed: every request must carry a Host
naming this machine locally (see Handler._host_ok), which is what stops a
DNS-rebinding page from reading the raw explorer from the open internet.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, urlsplit, parse_qs, unquote
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

# The payload routes answer only when the request names this user (?user=will),
# or when a front door forwards that name for us. This was never authentication
# — the server is LAN-only, no-auth by choice (see README) and the payload is
# derivable from the raw explorer anyway; the real door is the proxy's.
# 08-22: the page's matching UI lock (a name box and an Unlock button) was
# removed as deprecated — nothing reaches the tab that has not already come
# through the front door. The check stays here because it still costs nothing
# and still turns away a bare request that names nobody.
_PAYLOAD_USER = os.environ.get("MIRAI_PAYLOAD_USER", "will").strip().lower()


def _forwarded_user(headers) -> str | None:
    """The identity a front door (Caddy/nginx basic_auth or forward_auth) hands
    us, if any. The viewstation itself has no login; when a proxy authenticates
    and forwards the user name, these routes accept it in place of the ?user=
    query. Absent header → unknown (None)."""
    for h in ("X-Forwarded-User", "Remote-User", "X-Remote-User", "X-Auth-User"):
        v = headers.get(h)
        if v and v.strip():
            return v.strip().lower()
    return None


def _payload_unlocked(qs: dict) -> bool:
    """True iff the query names the one permitted user (case/space-insensitive)."""
    user = (qs.get("user") or [""])[0]
    return bool(_PAYLOAD_USER) and user.strip().lower() == _PAYLOAD_USER


# --- SNDK memory (08-21) — the model's history tool, run the way the model runs it
# The Memory view (behind the same lock) shows what the reading model can
# remember: the SNDK RAG store. The overview is a read-only inventory
# (snapshot.sndk_memory_overview); a query runs the SAME allow-listed CLI the
# model runs (skills/sndk-pro/sndk_rag.py, same interpreter, in a subprocess so
# the embedder never loads into this server) and hands back both the parsed
# result and the exact text the model would see. Note the CLI's own documented
# side effect: a days/month ask rolls closed sessions up into summaries.jsonl /
# terrain.json under state/sndk_rag — the memory maintaining itself, exactly as
# when the model asks; no trading state is touched.
_RAG_CLI = PLUGIN_ROOT / "skills" / "sndk-pro" / "sndk_rag.py"
_MEMORY_TIMEOUT_S = 60.0
_MEMORY_MAX_LIMIT = 60
_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM_RX = re.compile(r"^\d{2}:\d{2}$")


def _memory_args(qs: dict) -> list[str] | None:
    """Build the CLI argv from the query string, or None when it is malformed.
    Only the documented flags, every value validated — this is user input
    reaching a subprocess, even if the subprocess is ours."""
    kind = (qs.get("kind") or ["query"])[0]
    one = lambda k: (qs.get(k) or [""])[0].strip()
    argv: list[str] = []
    if kind == "series":
        argv.append("series")
        if one("date"):
            if not _DATE_RX.match(one("date")): return None
            argv += ["--date", one("date")]
        for flag, key in (("--from", "from"), ("--to", "to")):
            if one(key):
                if not _HHMM_RX.match(one(key)): return None
                argv += [flag, one(key)]
        if one("step"):
            if not one("step").isdigit() or not 1 <= int(one("step")) <= 120: return None
            argv += ["--step", one("step")]
        if one("strike"):
            try: float(one("strike"))
            except ValueError: return None
            argv += ["--strike", one("strike")]
        return argv
    if kind != "query":
        return None
    tier = one("tier") or "slices"
    if tier not in ("slices", "days", "month"): return None
    argv += ["query", "--tier", tier]
    for flag, key in (("--date", "date"), ("--from-date", "from_date"), ("--to-date", "to_date")):
        if one(key):
            if not _DATE_RX.match(one(key)): return None
            argv += [flag, one(key)]
    for flag, key in (("--from", "from"), ("--to", "to")):
        if one(key):
            if not _HHMM_RX.match(one(key)): return None
            argv += [flag, one(key)]
    for flag, key in (("--near-strike", "near"), ("--tolerance", "tol"), ("--min-move", "min_move")):
        if one(key):
            try: float(one(key))
            except ValueError: return None
            argv += [flag, one(key)]
    if one("text"):
        t = one("text")
        if len(t) > 240: return None
        argv += ["--text", t]
    if one("days_back"):
        if not one("days_back").isdigit() or not 1 <= int(one("days_back")) <= 60: return None
        argv += ["--days-back", one("days_back")]
    if one("limit"):
        if not one("limit").isdigit() or not 1 <= int(one("limit")) <= _MEMORY_MAX_LIMIT: return None
        argv += ["--limit", one("limit")]
    return argv


def _memory_query(qs: dict) -> dict:
    argv = _memory_args(qs)
    if argv is None:
        return {"error": "bad query"}
    cmd = [sys.executable, str(_RAG_CLI), *argv]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_MEMORY_TIMEOUT_S,
                           cwd=str(_RAG_CLI.parent))
    except subprocess.TimeoutExpired:
        return {"error": f"the history tool did not answer within {_MEMORY_TIMEOUT_S:.0f}s",
                "cmd": "sndk_rag.py " + " ".join(argv)}
    except OSError as exc:
        return {"error": f"could not run the history tool: {exc}"}
    wall = round(time.monotonic() - t0, 1)
    out = {"cmd": "sndk_rag.py " + " ".join(argv), "rc": r.returncode, "seconds": wall,
           "raw": (r.stdout or "")[-200_000:], "stderr": (r.stderr or "")[-2000:]}
    try:
        out["result"] = json.loads(r.stdout)
    except (ValueError, TypeError):
        out["result"] = None
    return out

# Extra Host names the tablet may use, beyond the local/private shapes allowed
# by Handler._host_ok (e.g. a public DNS name in front of a reverse proxy).
_EXTRA_HOSTS = {
    h.strip() for h in os.environ.get("MIRAI_VIEW_HOSTS", "").split(",") if h.strip()
}
# Tailscale tailnet addresses live in CGNAT space, which Python does not count
# as private; without this, reaching the view by tailnet IP would 403.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _local_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".local") or host.endswith(".ts.net"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # ::ffff:8.8.8.8 must be judged as 8.8.8.8. Some Python versions call the
    # mapped form private on the strength of the v6 prefix alone, which would
    # wave a public address straight through a check written to stop exactly
    # that.
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or (ip.version == 4 and ip in _CGNAT)

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


# The explorer's readable types. This lives here, and is enforced by BOTH the
# index and the fetch, because having it on the listing alone was worth nothing:
# the listing is a convenience, the fetch is the actual permission, and anything
# the fetch does not check is readable by anyone who can guess a name.
_RAW_SUFFIXES = (".json", ".jsonl", ".txt", ".md")

# Subtrees the explorer never exposes even though they sit under a readable
# root. The voice logs are verbatim recordings of the operator talking, and a
# live agent session id — a different category of private than a diary row.
_RAW_DENY = ("voice",)


def _raw_denied(root: Path, path: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return bool(parts) and parts[0] in _RAW_DENY


def _raw_index() -> dict:
    out = {}
    for label, root in RAW_ROOTS.items():
        items = []
        if root.exists():
            for p in sorted(root.rglob("*")):
                if p.is_file() and p.suffix in _RAW_SUFFIXES and not _raw_denied(root, p):
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
    if path.suffix not in _RAW_SUFFIXES or _raw_denied(root, path):
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


# --- recent-requests ring (08-21) ----------------------------------------------
# A small in-memory record of the last requests (time, path, Host, client, UA,
# status), behind the payload route's name check at /api/_access?user=will. Diagnostic only —
# the answer to "is my tablet actually polling, and what is it asking?" without
# turning on access logging for good. Nothing is written to disk.
import collections
_ACCESS = collections.deque(maxlen=300)
_ACCESS_LOCK = threading.RLock()   # re-entrant: the access route itself is noted while it answers


def _note_access(handler, status: int) -> None:
    try:
        with _ACCESS_LOCK:
            _ACCESS.append({
                "t": datetime.now(_ET).strftime("%H:%M:%S"),
                "path": handler.path[:120],
                "host": (handler.headers.get("Host") or "")[:80],
                "client": handler.client_address[0] if handler.client_address else None,
                "ua": (handler.headers.get("User-Agent") or "")[:90],
                "fwd": (handler.headers.get("X-Forwarded-For") or "")[:60],
                "status": status,
            })
    except Exception:
        pass


# --- page version (08-21) -------------------------------------------------------
# The viewstation has no version number of its own, so the page's modified-time
# stands in for one: any save / pull / deploy that touches index.html gives it a
# new mtime. The open pages poll this every 10 s and offer a refresh when it
# changes (see the reload pill in index.html). mtime, not a hash: one stat call,
# no file read, and "changed" is all the tablet needs to know.
def _page_version() -> str:
    try:
        return str((STATIC / "index.html").stat().st_mtime_ns)
    except OSError:
        return "0"


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

    def _host_ok(self) -> bool:
        """Reject requests not addressed to this machine by a LAN/local name.

        "LAN-only" is an assumption about the network, and DNS rebinding breaks
        it without touching the network: an attacker's page re-resolves its own
        domain to this box's private IP, so the browser treats the reply as
        same-origin and can read /api/raw/file — the diary, the push log, the
        voice transcripts. The Host header still says the attacker's domain,
        which is what this catches. Shape-based, so a new DHCP lease or tailnet
        name keeps working; extra names via MIRAI_VIEW_HOSTS (csv).
        """
        raw = self.headers.get("Host") or ""
        if not raw:
            # HTTP/1.1 makes Host mandatory and no browser speaks 1.0, so an
            # absent Host is a raw-socket caller. Refusing it also closes the
            # absolute-form request line (GET http://evil.com/... HTTP/1.1),
            # which otherwise skips this check by carrying no Host at all.
            return self.request_version < "HTTP/1.1"
        host = urlsplit("//" + raw).hostname or ""
        return host in _EXTRA_HOSTS or _local_host(host)

    def _same_site_ok(self) -> bool:
        """CSRF guard for the one write route — the job _host_ok cannot do.

        A page on evil.com that fetches straight to http://127.0.0.1:8787/...
        makes the browser send `Host: 127.0.0.1:8787` — the fetch target, not
        the page — so the Host check passes and always will. What does give the
        caller away is Origin, which browsers attach to every cross-origin POST,
        and Sec-Fetch-Site. A caller sending neither is not a browser, and a
        non-browser gains nothing here it could not do with a raw socket.
        """
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site not in ("same-origin", "same-site", "none"):
            return False
        origin = self.headers.get("Origin")
        if origin is None:                    # non-browser client
            return True
        # 20-agent sweep 08-11: an operator-declared MIRAI_VIEW_HOSTS name is a
        # first-party origin too — _host_ok admits its GETs, so refusing its
        # same-origin POST here 403'd the reasoning toggle on any proxied name.
        o_host = urlsplit(origin).hostname or ""
        return o_host in _EXTRA_HOSTS or _local_host(o_host)

    def _send_json(self, obj, status=200):
        _note_access(self, status)
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _deny(self, msg="bad host"):
        """Refuse a request without leaving its body on the socket.

        Rejecting before reading the body is what makes a 403 smuggleable: the
        unread bytes stay buffered, and on a keep-alive connection the next
        parse reads them as a *fresh* request — one that arrives with no Origin
        and therefore sails through both guards. Closing the connection is what
        makes the refusal actually stick.
        """
        self.close_connection = True
        self._send_json({"error": msg}, 403)

    def _send_file(self, path: Path):
        if not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        _note_access(self, 200)
        self.send_response(200)
        self.send_header("Content-Type",
                         _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._deny()
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

            if route == "/api/version":
                # the page's own build id (index.html mtime) — polled by every open page
                return self._send_json({"v": _page_version()})

            if route == "/api/sndk/reasoning":
                return self._send_json(_reasoning_state())

            if route == "/api/_access":
                if not _payload_unlocked(qs) and _forwarded_user(self.headers) != _PAYLOAD_USER:
                    return self._send_json({"error": "locked"}, 403)
                with _ACCESS_LOCK:
                    recent = list(_ACCESS)          # copy INSIDE the lock, send OUTSIDE it — _send_json notes the request too
                return self._send_json({"recent": recent})

            if route == "/api/whoami":
                # who the front door says you are (None without a proxy) and whether that is the
                # permitted user. Diagnostic only since 08-22 — the page stopped consuming it when
                # the payload lock was removed; it stays because it is the one way to ask, from a
                # browser, which user the proxy thinks is looking (two machines, two logins).
                u = _forwarded_user(self.headers)
                return self._send_json({"user": u, "permitted": bool(u) and u == _PAYLOAD_USER,
                                        "permitted_user": _PAYLOAD_USER})

            if route == "/api/sndk/memory":
                # the model's memory (SNDK RAG store) — same lock as the payload
                if not _payload_unlocked(qs) and _forwarded_user(self.headers) != _PAYLOAD_USER:
                    return self._send_json({"error": "locked"}, 403)
                try:
                    kind = (qs.get("kind") or ["query"])[0]
                    if kind == "overview":
                        return self._send_json(snap.sndk_memory_overview())
                    return self._send_json(_memory_query(qs))
                except Exception as exc:  # never 500 the tablet
                    import traceback
                    return self._send_json({"error": f"{type(exc).__name__}: {exc}",
                                            "trace": traceback.format_exc()})

            if route == "/api/sndk/payload":
                # the exact scene the reader hands the model — locked to one user (the ?user= name,
                # or the front door's forwarded user when a proxy authenticates for us)
                if not _payload_unlocked(qs) and _forwarded_user(self.headers) != _PAYLOAD_USER:
                    return self._send_json({"error": "locked"}, 403)
                try:
                    return self._send_json(snap.sndk_payload())
                except Exception as exc:  # never 500 the tablet
                    import traceback
                    return self._send_json({"error": f"{type(exc).__name__}: {exc}",
                                            "trace": traceback.format_exc()})

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
        if not self._host_ok() or not self._same_site_ok():
            return self._deny()
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
