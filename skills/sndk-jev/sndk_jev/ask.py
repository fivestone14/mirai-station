"""Pack a state into JEV requests and, when asked, send them.

One request per question group. A request carries only the slice of the
state the group reads, and only the questions whose backticked paths are all
present. A question that needs a missing label is skipped and the reason is
kept, so a thin scan never turns into a forced answer.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"
PATH_RE = re.compile(r"`([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)`")
DEFAULT_QUESTIONS = Path(__file__).resolve().parent.parent / "questions" / "sndk_pro.json"


def load_questions(path: Path | str = DEFAULT_QUESTIONS) -> dict:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc.get("groups"), list):
        raise ValueError(f"{path} has no groups list")
    return doc


def get_path(state: dict, path: str) -> Any:
    cur: Any = state
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    if isinstance(cur, dict) and not cur:
        return None
    return cur


def set_path(target: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = target
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


JEV_KEYS = ("type", "instructions", "criteria")


def jev_only(question: dict) -> dict:
    """The part of a question JEV receives. Everything else (viewpoint, why, ask, status) is for people."""
    return {k: question[k] for k in JEV_KEYS if k in question}


def paths_in(question: dict) -> list[str]:
    seen: list[str] = []
    for m in PATH_RE.finditer(json.dumps(jev_only(question), ensure_ascii=False)):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def build_requests(state: dict, doc: dict, skip: dict[str, str] | None = None) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """Return ``(requests, skipped)``.

    ``requests`` is a list of ``{"id", "state", "questions"}`` ready for JEV.
    ``skipped`` maps group id to ``{question id: reason}`` for anything left out,
    including whole groups under the key ``"*"``. ``skip`` names questions to leave
    out with a reason of the caller's own (the cadence plan uses it).
    """
    requests: list[dict] = []
    skipped: dict[str, dict[str, str]] = {}
    skip = skip or {}
    for group in doc["groups"]:
        gid = group["id"]
        slice_: dict = {}
        # every request carries the context group, so JEV always sees the symbol, the units and the horizon
        reads = ["context"] + [p for p in group.get("reads", []) if p != "context" and not p.startswith("context.")]
        for path in reads:
            v = get_path(state, path)
            if v is not None:
                set_path(slice_, path, v)
        questions: dict = {}
        for qid, q in group.get("questions", {}).items():
            if q.get("status") == "dark":
                # dark: its source is not plugged in, so it is neither asked nor counted, whatever the state holds
                skipped.setdefault(gid, {})[qid] = "dark: no news source is plugged in yet"
                continue
            if qid in skip:
                skipped.setdefault(gid, {})[qid] = skip[qid]
                continue
            missing = [p for p in paths_in(q) if get_path(state, p) is None]
            # a label the state has but this group does not read would leave JEV blind to it
            unread = [p for p in paths_in(q) if p not in missing and get_path(slice_, p) is None]
            if missing:
                skipped.setdefault(gid, {})[qid] = "missing " + ", ".join(missing)
            elif unread:
                skipped.setdefault(gid, {})[qid] = "the group does not read " + ", ".join(unread)
            else:
                questions[qid] = jev_only(q)
        if not questions:
            skipped.setdefault(gid, {})["*"] = "nothing to ask in this group this read"
            continue
        requests.append({"id": gid, "state": slice_, "questions": questions})
    return requests, skipped


RETRY_HTTP = (429, 500, 502, 503, 504)    # a second try is worth it; a 4xx is not


def _scrub(text: str, key: str | None) -> str:
    """The key must never reach a log, a state file or an exception message."""
    return text.replace(key, "[key redacted]") if key else text


def send(request: dict, api_key: str | None = None, timeout: float = 10.0, url: str = JEV_URL,
         model: str = JEV_MODEL, retries: int = 1) -> dict:
    """POST one request to JEV and return the parsed answer. One more try on a timeout, a dropped
    connection or a 429/5xx. Every failure becomes a RuntimeError with the key scrubbed out, so a
    caller that catches RuntimeError has caught everything the network can throw."""
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(f"set {API_KEY_ENV} in the environment before sending")
    body = json.dumps({"model": model, "state": request["state"], "questions": request["questions"]}).encode("utf-8")
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # the body is quoted in the error; scrub before the cut so a key can never straddle it
            detail = _scrub(e.read().decode("utf-8", errors="replace"), key)[:400]
            if e.code in RETRY_HTTP and attempt < retries:
                attempt += 1
                continue
            raise RuntimeError(f"JEV returned HTTP {e.code} for group {request['id']}: {detail}") from None
        except (OSError, http.client.HTTPException) as e:
            # a timeout, a refused or dropped connection, a failed handshake (URLError is an OSError)
            if attempt < retries:
                attempt += 1
                continue
            raise RuntimeError(f"JEV unreachable for group {request['id']}: {type(e).__name__}: {_scrub(str(e), key)[:200]}") from None
        except ValueError as e:
            raise RuntimeError(f"JEV answered group {request['id']} with something that is not JSON: {_scrub(str(e), key)[:200]}") from None


def send_all(requests: list[dict], api_key: str | None = None, timeout: float = 10.0, workers: int = 12,
             sender=None) -> dict[str, dict]:
    """POST every request at the same time and return ``{request id: answer or {"error": ...}}``.

    JEV scores each question independently and the requests share nothing, so the
    round trips of a read collapse into one wait. A failed request records its
    error and never blocks the others, whatever the failure was.
    """
    from concurrent.futures import ThreadPoolExecutor
    sender = sender or send
    if not requests:
        return {}
    key = api_key or os.environ.get(API_KEY_ENV)

    def one(req: dict) -> tuple[str, dict]:
        try:
            return req["id"], sender(req, api_key=api_key, timeout=timeout)
        except Exception as e:  # one group must never take the whole read down
            msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            return req["id"], {"error": _scrub(msg, key)}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(requests)))) as ex:
        for rid, ans in ex.map(one, requests):
            out[rid] = ans
    return out


def summarize(answers: dict) -> list[str]:
    """One line per answer: the pick and its probability, or the score and confidence."""
    lines = []
    for qid, a in (answers.get("answers") or {}).items():
        if not isinstance(a, dict):
            continue
        t = a.get("type")
        if t == "noul":
            lines.append(f"{qid}: yes {a.get('noul', 0):.2f}")
        elif t == "choice":
            probs = a.get("probabilities") or {}
            top = a.get("choice")
            lines.append(f"{qid}: {top} {probs.get(top, 0):.2f}  confidence {a.get('confidence', 0):.2f}")
        elif t == "score":
            lines.append(f"{qid}: score {a.get('score', 0):.2f}  confidence {a.get('confidence', 0):.2f}  {a.get('probabilities')}")
        else:
            lines.append(f"{qid}: {a}")
    return lines
