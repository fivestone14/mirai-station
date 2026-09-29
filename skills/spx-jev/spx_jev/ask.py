"""Pack a state into JEV requests and, when asked, send them.

One request per question group, at most GROUP_CAP questions each. A request
carries only the slice of the state the group reads, and only the questions
that are asked this read: live and shadow ones (a dark one never), due on
their schedule (the caller's ``skip``, from schedule.not_due and the cadence),
awake (a question with ``sleep_when`` is asked only when its label family says
it is awake, so its "nothing happened" default never reaches the weights) and
whose backticked paths are all present. A question left out keeps its reason,
so a thin scan never turns into a forced answer.

The question docs carry no numbers of their own: every threshold in their text
is a name in braces ("{move_rule_sigma}") filled from cuts.QUESTION_CONSTANTS
when the doc is loaded, so the words JEV reads and the cut the code judged by
are the same number.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from string import Formatter
from typing import Any

from .cuts import QUESTION_CONSTANTS
from .schedule import check as check_schedule

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
API_KEY_ENV = "TYPESAFE_API_KEY"
PATH_RE = re.compile(r"`([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)`")
DEFAULT_QUESTIONS = Path(__file__).resolve().parent.parent / "questions" / "spx_questions.json"
# the fields whose text may name a constant
TEMPLATED_KEYS = ("ask", "instructions", "criteria", "code_criteria", "sleep_when")
GROUP_CAP = 8                    # questions one JEV request may carry


def constants_named(text: str) -> list[str]:
    """The constant names a question's text asks for, in braces."""
    return [name for _, name, _, _ in Formatter().parse(text) if name is not None]


def fill(value: Any, where: str) -> Any:
    """``value`` with every ``{name}`` replaced by the constant of that name. A name the code does not
    define is an error naming the question, so a doc can never go out with a hole or a guess in it."""
    if isinstance(value, str):
        unknown = [n for n in constants_named(value) if n not in QUESTION_CONSTANTS]
        if unknown:
            raise ValueError(f"{where} names {', '.join(unknown)}, which cuts.QUESTION_CONSTANTS does not define")
        return value.format_map(QUESTION_CONSTANTS)
    if isinstance(value, dict):
        return {k: fill(v, where) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, where) for v in value]
    return value


def fill_question(question: dict, where: str) -> dict:
    return {k: fill(v, where) if k in TEMPLATED_KEYS else v for k, v in question.items()}


def for_lane(question: dict, lane_key: str) -> dict:
    """A question as one lane asks it: that lane's schedule entry and horizon in place of the per-lane maps."""
    out = dict(question)
    if isinstance(question.get("schedule"), dict):
        out["schedule"] = question["schedule"].get(lane_key)
    if isinstance(question.get("horizon"), dict):
        out["horizon"] = question["horizon"].get(lane_key)
    return out


def load_questions(path: Path | str = DEFAULT_QUESTIONS, lane_key: str | None = None) -> dict:
    """A step-2 question doc with its constants filled in and every schedule checked (schedule.check).
    With ``lane_key`` only the questions whose ``lanes`` name it are kept (a question naming no lanes
    serves every lane), each with that lane's schedule entry and horizon; a group left empty is dropped.
    A group over GROUP_CAP questions stops the load."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc.get("groups"), list):
        raise ValueError(f"{path} has no groups list")
    for group in doc["groups"]:
        questions = group.get("questions", {})
        if len(questions) > GROUP_CAP:
            raise ValueError(f"{path} group {group.get('id')} has {len(questions)} questions, over the cap of {GROUP_CAP} a request may carry")
        for qid, q in questions.items():
            for lane, entry in (q.get("schedule") or {}).items():
                check_schedule(entry, f"{Path(path).name} {qid} ({lane})")
        group["questions"] = {qid: fill_question(q if lane_key is None else for_lane(q, lane_key), f"{Path(path).name} {qid}")
                              for qid, q in questions.items() if lane_key is None or lane_key in q.get("lanes", [lane_key])}
    if lane_key is not None:
        doc["groups"] = [g for g in doc["groups"] if g["questions"]]
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


NO_GATE = "no label family decides its gate"


def build_requests(state: dict, doc: dict, skip: dict[str, str] | None = None,
                   gates: dict[str, str | None] | None = None,
                   ended: dict[str, str] | None = None) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """Return ``(requests, skipped)``.

    ``requests`` is a list of ``{"id", "state", "questions"}`` ready for JEV.
    ``skipped`` maps group id to ``{question id: reason}`` for anything left out,
    including whole groups under the key ``"*"``. ``skip`` names questions to leave
    out with a reason of the caller's own (the schedule and the cadence). ``gates`` is
    the labels' sleep gates (LabelSet.gates): a question with ``sleep_when`` is asked
    only when its gate says it is awake. ``ended`` is the labels left out because what
    they describe is not there (LabelSet.ended_reasons): a question missing one of them
    is asleep on its reason, since nothing happened, rather than missing a label.
    """
    requests: list[dict] = []
    skipped: dict[str, dict[str, str]] = {}
    skip = skip or {}
    gates = gates or {}
    ended = ended or {}
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
                skipped.setdefault(gid, {})[qid] = "dark: the source it needs is not plugged in"
                continue
            if qid in skip:
                skipped.setdefault(gid, {})[qid] = skip[qid]
                continue
            if q.get("sleep_when") and gates.get(qid, NO_GATE) is not None:
                # asleep: its default "nothing happened" state holds, and a default must never reach the weights
                skipped.setdefault(gid, {})[qid] = f"asleep: {gates.get(qid, NO_GATE)}"
                continue
            missing = [p for p in paths_in(q) if get_path(state, p) is None]
            # a label the state has but this group does not read would leave JEV blind to it
            unread = [p for p in paths_in(q) if p not in missing and get_path(slice_, p) is None]
            over = next((ended[p] for p in missing if p in ended), None)
            if over is not None:
                skipped.setdefault(gid, {})[qid] = f"asleep: {over}"
            elif missing:
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


RETRY_HTTP = (429, 500, 502, 503, 504, 529)    # another try is worth it; a 4xx is not (529 is TypeSafe's "overloaded")
RETRIES = 2
# TypeSafe asks for a pause before retrying a 429 or 529 rather than an immediate retry: the first retry waits
# its Retry-After, else RETRY_WAIT_S, never more than RETRY_WAIT_MAX_S; a timeout or a dropped connection is
# retried at once.
RETRY_WAIT_S = 1.0
RETRY_WAIT_MAX_S = 2.0
# Every later retry comes a few seconds on, whatever failed: on 09-28 a try straight after a failure often met
# the same stuck or overloaded JEV, and a few seconds later it had cleared. Its Retry-After is honoured within
# these bounds, so a five-minute read is never held up for long.
LAST_RETRY_WAIT_S = 4.0
LAST_RETRY_WAIT_MIN_S = 3.0
LAST_RETRY_WAIT_MAX_S = 5.0


def retry_wait(headers, attempt: int = 1) -> float:
    """Seconds to wait before retry number ``attempt``: an HTTP error's Retry-After in seconds within the
    retry's bounds, else the retry's own pause. ``headers`` is None for a timeout or a dropped connection,
    which the first retry does not wait for."""
    late = attempt > 1
    if headers is None and not late:
        return 0.0
    low, default, high = (LAST_RETRY_WAIT_MIN_S, LAST_RETRY_WAIT_S, LAST_RETRY_WAIT_MAX_S) if late else (0.0, RETRY_WAIT_S, RETRY_WAIT_MAX_S)
    try:
        return max(low, min(float((headers or {}).get("Retry-After")), high))
    except (TypeError, ValueError):
        return default


def _scrub(text: str, key: str | None) -> str:
    """The key must never reach a log, a state file or an exception message."""
    return text.replace(key, "[key redacted]") if key else text


def send(request: dict, api_key: str | None = None, timeout: float = 10.0, url: str = JEV_URL,
         model: str = JEV_MODEL, retries: int = RETRIES, deadline: float | None = None) -> dict:
    """POST one request to JEV and return the parsed answer. Up to ``retries`` more tries on a timeout, a
    dropped connection or a 429/5xx, each after its pause (retry_wait); with a ``deadline`` (a time.monotonic()
    reading) a retry is made only when its pause and a full ``timeout`` end by it. Every failure becomes a
    RuntimeError with the key scrubbed out, so a caller that catches RuntimeError has caught everything the
    network can throw."""
    key = api_key or os.environ.get(API_KEY_ENV)
    if not key:
        raise RuntimeError(f"set {API_KEY_ENV} in the environment before sending")
    body = json.dumps({"model": model, "state": request["state"], "questions": request["questions"]}).encode("utf-8")

    def another_try(attempt: int, headers) -> bool:
        """Pause for retry ``attempt`` and say yes, or say no when there is none left or no time for it."""
        if attempt > retries:
            return False
        pause = retry_wait(headers, attempt)
        if deadline is not None and time.monotonic() + pause + timeout > deadline:
            return False
        if pause:
            time.sleep(pause)
        return True

    attempt = 0
    while True:
        attempt += 1
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
            if e.code in RETRY_HTTP and another_try(attempt, e.headers or {}):
                continue
            raise RuntimeError(f"JEV returned HTTP {e.code} for group {request['id']}{_tries(attempt)}: {detail}") from None
        except (OSError, http.client.HTTPException) as e:
            # a timeout, a refused or dropped connection, a failed handshake (URLError is an OSError)
            if another_try(attempt, None):
                continue
            raise RuntimeError(f"JEV unreachable for group {request['id']}{_tries(attempt)}: {type(e).__name__}: {_scrub(str(e), key)[:200]}") from None
        except ValueError as e:
            raise RuntimeError(f"JEV answered group {request['id']} with something that is not JSON: {_scrub(str(e), key)[:200]}") from None


def _tries(n: int) -> str:
    return f" after {n} tries" if n > 1 else ""


def send_all(requests: list[dict], api_key: str | None = None, timeout: float = 10.0, workers: int = 12,
             sender=None, deadline: float | None = None) -> dict[str, dict]:
    """POST every request at the same time and return ``{request id: answer or {"error": ...}}``.

    JEV scores each question independently and the requests share nothing, so the
    round trips of a read collapse into one wait. A failed request records its
    error and never blocks the others, whatever the failure was. ``deadline`` is
    send's, for every request.
    """
    from concurrent.futures import ThreadPoolExecutor
    sender = sender or send
    if not requests:
        return {}
    key = api_key or os.environ.get(API_KEY_ENV)

    def one(req: dict) -> tuple[str, dict]:
        try:
            return req["id"], sender(req, api_key=api_key, timeout=timeout, deadline=deadline)
        except Exception as e:  # one group must never take the whole read down
            msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            return req["id"], {"error": _scrub(msg, key)}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(requests)))) as ex:
        for rid, ans in ex.map(one, requests):
            out[rid] = ans
    return out


def pick(answer: dict) -> str | None:
    """What JEV chose, as an option name."""
    if not isinstance(answer, dict):
        return None
    if answer.get("type") == "choice":
        return answer.get("choice")
    if answer.get("type") == "noul":
        n = answer.get("noul")
        return None if not isinstance(n, (int, float)) else ("true" if n >= 0.5 else "false")
    if answer.get("type") == "score":
        # the level with the most probability, as its position in the ladder; the single
        # score is a weighted mean and can sit between two levels, so it is never used here
        probs = answer.get("probabilities")
        if isinstance(probs, dict) and probs:
            return str(max(probs, key=lambda k: probs[k]))
        if isinstance(probs, list) and probs:
            return str(max(range(len(probs)), key=lambda i: probs[i]))
    return None


def confidence(answer: dict) -> float | None:
    """How sure JEV said it was: its own confidence on a choice or a score, the distance from a coin on a yes/no."""
    if not isinstance(answer, dict):
        return None
    if answer.get("type") in ("choice", "score"):
        c = answer.get("confidence")
        return float(c) if isinstance(c, (int, float)) else None
    if answer.get("type") == "noul":
        n = answer.get("noul")
        return abs(float(n) - 0.5) * 2 if isinstance(n, (int, float)) else None
    return None


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
