"""The Schwab feeds' log lines (bars.py, market_context.py): every line stamped with the UTC clock and its
offset, as the service's are, so a failure is dated by its own line and not by its place in the file, and
a failed call named with what Schwab answered, any token scrubbed out.

A failed renewal of the access token reaches a feed as authlib's OAuthError, which keeps only the error
and its description; the reply itself, its status and its whole body, is still a local of the authlib
frame that raised (``parse_response_token(self, resp)``), so it is read from there. A failed data call
reaches a feed as httpx's HTTPStatusError, which carries the reply. Neither library is imported here.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

BODY_CHARS = 400            # of Schwab's reply, cut after scrubbing so a token can never straddle the cut
_TOKEN_FIELD = re.compile(r'("?\b(?:access_token|refresh_token|id_token|client_secret|code)"?\s*[:=]\s*)"?[^",&\s}]+"?', re.I)
_BEARER = re.compile(r"(Bearer\s+)\S+", re.I)
_TOKEN_LIKE = re.compile(r"[A-Za-z0-9._~+/=@-]{40,}")    # a run this long in a reply is a token, never words


def log(job: str, msg: str, err: bool = False) -> None:
    """One line, ``<UTC ISO time> <job> :: <msg>``, to stderr when ``err``, else stdout."""
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {job} :: {msg}", file=sys.stderr if err else sys.stdout)


def scrub(text: str) -> str:
    """``text`` with every token Schwab could echo replaced, keyed or bare."""
    text = _TOKEN_FIELD.sub(r"\1[redacted]", text)
    text = _BEARER.sub(r"\1[redacted]", text)
    return _TOKEN_LIKE.sub("[redacted]", text)


def _reply_of(e: BaseException):
    """The HTTP reply behind ``e``: its own ``response``, or the ``resp`` local of the deepest frame that
    raised it holding one (authlib's). None when there is none."""
    reply = getattr(e, "response", None)
    if hasattr(reply, "status_code"):
        return reply
    tb, found = e.__traceback__, None
    while tb is not None:
        cand = tb.tb_frame.f_locals.get("resp")
        if hasattr(cand, "status_code") and hasattr(cand, "text"):
            found = cand
        tb = tb.tb_next
    return found


def failure(e: BaseException) -> str:
    """``<type>: <message>``, and when Schwab replied, ``; Schwab HTTP <status>: <body>``, scrubbed."""
    out = f"{type(e).__name__}: {scrub(str(e))}"
    reply = _reply_of(e)
    if reply is None:
        return out
    try:
        body = reply.text
    except Exception as read_error:  # a streamed reply that was never read has no text to give
        body = f"<unreadable: {type(read_error).__name__}>"
    return f"{out}; Schwab HTTP {reply.status_code}: {scrub(' '.join(str(body).split()))[:BODY_CHARS]}"
