"""Tests for the OAuth refresh path (2026-09-09).

None of these touch the real Keychain, the real lock or the network: `vault` is
swapped for an in-memory dict and every HTTP round-trip is a canned response.
The lock is redirected to tmp_path so a test run can never block, or be blocked
by, a live scan mid-refresh.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cassandra_oauth as co
import native_gex_feed as tf


class FakeVault:
    """The three Keychain slots, in a dict."""

    class CredentialsNotEnrolled(RuntimeError):
        pass

    def __init__(self, refresh=None, access=None, client=None):
        self.refresh, self.access, self.client = refresh, access, client
        self.static_cleared = False

    def install_runtime_hardening(self):
        pass

    def get_cassandra_refresh_token(self):
        if self.refresh is None:
            raise self.CredentialsNotEnrolled("not enrolled")
        return self.refresh

    def set_cassandra_refresh_token(self, tok):
        self.refresh = tok

    def has_cassandra_refresh_token(self):
        return self.refresh is not None

    def get_cassandra_access(self):
        return self.access

    def set_cassandra_access(self, token, expires_at):
        self.access = {"token": token, "expires_at": expires_at}

    def get_cassandra_client(self):
        return self.client

    def set_cassandra_client(self, blob):
        self.client = blob

    def wipe_cassandra_oauth(self):
        self.refresh = self.access = self.client = None

    def clear_cassandra_token(self):
        self.static_cleared = True


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected raise_for_status on {self.status_code}")


CLIENT_BLOB = {"issuer": "https://as.example", "client_id": "cid-1", "client_secret": None,
               "authorization_endpoint": "https://as.example/oauth2/authorize",
               "token_endpoint": "https://as.example/oauth2/token",
               "redirect_uri": "http://127.0.0.1:8765/callback"}


@pytest.fixture
def oauth_env(tmp_path, monkeypatch):
    """A stubbed vault + a lock nobody else holds. Returns the fake vault."""
    fv = FakeVault(refresh="rt-original", client=dict(CLIENT_BLOB))
    monkeypatch.setattr(co, "vault", fv)
    monkeypatch.setattr(co, "_LOCK_PATH", tmp_path / "oauth.lock")
    monkeypatch.setattr(co, "_HARDENED", True)
    return fv


def _fake_http(monkeypatch, posts):
    """Wire co._client() to a client whose .post pops from `posts` (a list of
    (assertion_fn, FakeResponse)) and records every call."""
    calls = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, data=None, json=None):
            calls.append({"url": url, "data": data or {}, "json": json})
            check, resp = posts.pop(0)
            if check:
                check(url, data or {})
            return resp

        def get(self, url):
            calls.append({"url": url, "data": {}, "json": None})
            check, resp = posts.pop(0)
            if check:
                check(url, {})
            return resp

    monkeypatch.setattr(co, "_client", lambda: FakeClient())
    return calls


# ---------------------------------------------------------------------------
# the cache
# ---------------------------------------------------------------------------
def test_live_cached_token_costs_no_round_trip(oauth_env, monkeypatch):
    oauth_env.access = {"token": "at-live", "expires_at": time.time() + 240}
    _fake_http(monkeypatch, [])                      # any HTTP call pops an empty list → IndexError
    assert co.bearer() == "at-live"


def test_token_inside_skew_is_refreshed_early(oauth_env, monkeypatch):
    # 30s left is inside the 60s skew: a scan handed this token could outlive it.
    oauth_env.access = {"token": "at-dying", "expires_at": time.time() + 30}
    calls = _fake_http(monkeypatch, [
        (None, FakeResponse(200, {"access_token": "at-fresh", "expires_in": 300,
                                  "refresh_token": "rt-rotated"})),
    ])
    assert co.bearer() == "at-fresh"
    assert calls[0]["data"]["grant_type"] == "refresh_token"


def test_expired_cache_refreshes(oauth_env, monkeypatch):
    oauth_env.access = {"token": "at-dead", "expires_at": time.time() - 1}
    _fake_http(monkeypatch, [
        (None, FakeResponse(200, {"access_token": "at-fresh", "expires_in": 300})),
    ])
    assert co.bearer() == "at-fresh"
    assert oauth_env.access["token"] == "at-fresh"
    assert oauth_env.access["expires_at"] > time.time() + 290


# ---------------------------------------------------------------------------
# rotation — the failure that would strand the station overnight
# ---------------------------------------------------------------------------
def test_rotated_refresh_token_is_persisted(oauth_env, monkeypatch):
    _fake_http(monkeypatch, [
        (None, FakeResponse(200, {"access_token": "at-1", "expires_in": 300,
                                  "refresh_token": "rt-rotated"})),
    ])
    co.bearer()
    assert oauth_env.refresh == "rt-rotated", "a rotated refresh token must replace the old one"


def test_refresh_kept_when_server_does_not_rotate(oauth_env, monkeypatch):
    _fake_http(monkeypatch, [
        (None, FakeResponse(200, {"access_token": "at-1", "expires_in": 300})),
    ])
    co.bearer()
    assert oauth_env.refresh == "rt-original", "no new token means keep the one we have"


def test_resource_parameter_is_sent(oauth_env, monkeypatch):
    """RFC 8707. Without it AuthKit mints a token the MCP endpoint will not
    accept, which looks exactly like a bad refresh token from the caller."""
    seen = {}
    calls = _fake_http(monkeypatch, [
        (lambda url, data: seen.update(data),
         FakeResponse(200, {"access_token": "at-1", "expires_in": 300})),
    ])
    co.bearer()
    assert seen["resource"] == co.RESOURCE
    assert seen["client_id"] == "cid-1"
    assert calls[0]["url"] == CLIENT_BLOB["token_endpoint"]


# ---------------------------------------------------------------------------
# failure modes
# ---------------------------------------------------------------------------
def test_invalid_grant_is_needs_login_not_a_retry(oauth_env, monkeypatch):
    _fake_http(monkeypatch, [(None, FakeResponse(400, {"error": "invalid_grant"}))])
    with pytest.raises(co.NeedsLogin):
        co.bearer()


def test_transient_error_is_not_needs_login(oauth_env, monkeypatch):
    """A 500 must NOT tell the operator to re-enrol — the refresh token is fine."""
    _fake_http(monkeypatch, [(None, FakeResponse(503, {}))])
    with pytest.raises(co.OAuthError) as e:
        co.bearer()
    assert not isinstance(e.value, co.NeedsLogin)


def test_not_enrolled_raises_before_any_network(oauth_env, monkeypatch):
    oauth_env.refresh = None
    _fake_http(monkeypatch, [])
    with pytest.raises(FakeVault.CredentialsNotEnrolled):
        co.bearer()


# ---------------------------------------------------------------------------
# the race the lock exists for
# ---------------------------------------------------------------------------
def test_winner_of_the_lock_race_is_reused(oauth_env, monkeypatch):
    """Two scans want a token at once. The loser must find the winner's token
    when it acquires the lock and NOT spend its own (by then retired) refresh
    token — so the second caller makes no HTTP call at all."""
    reads = iter([None, {"token": "at-from-winner", "expires_at": time.time() + 290}])
    monkeypatch.setattr(co, "_cached_bearer",
                        lambda: (lambda b: b["token"] if b else None)(next(reads)))
    _fake_http(monkeypatch, [])                      # any request → IndexError
    assert co.bearer() == "at-from-winner"


def test_lock_timeout_falls_back_to_a_stale_token(oauth_env, monkeypatch):
    """Better a token a few seconds past our safety margin than a scan with no
    dealer map at all — the endpoint is the judge of whether it still works."""
    from filelock import Timeout as LockTimeout

    class Boom:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            raise LockTimeout("busy")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("filelock.FileLock", Boom)
    oauth_env.access = {"token": "at-stale", "expires_at": time.time() + 5}
    assert co.bearer() == "at-stale"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def test_resource_metadata_url_from_challenge_header(oauth_env, monkeypatch):
    hdr = ('Bearer error="invalid_token", error_description="nope", '
           'resource_metadata="https://rs.example/.well-known/oauth-protected-resource/mcp"')
    _fake_http(monkeypatch, [(None, FakeResponse(401, {}, {"www-authenticate": hdr}))])
    with co._client() as c:
        assert co._resource_metadata_url(c) == \
            "https://rs.example/.well-known/oauth-protected-resource/mcp"


def test_resource_metadata_url_falls_back_to_well_known(oauth_env, monkeypatch):
    _fake_http(monkeypatch, [(None, FakeResponse(401, {}, {}))])
    with co._client() as c:
        url = co._resource_metadata_url(c)
    assert url.endswith("/.well-known/oauth-protected-resource/mcp")
    assert "market-research.cassandrasedge.com" in url


# ---------------------------------------------------------------------------
# native_gex_feed's one forced retry
# ---------------------------------------------------------------------------
@pytest.fixture
def feed_env(monkeypatch):
    monkeypatch.setattr(tf, "_SESSION", "sess-1")
    monkeypatch.setattr(tf, "_FORCE_REFRESH", False)
    monkeypatch.setattr(tf.cassandra_oauth, "is_enrolled", lambda: True)
    forced = []
    monkeypatch.setattr(tf.cassandra_oauth, "bearer",
                        lambda force_refresh=False: (forced.append(force_refresh), "at")[1])
    monkeypatch.setattr(tf, "_initialize", lambda: None)
    return forced


def test_401_triggers_exactly_one_forced_refresh_then_succeeds(feed_env, monkeypatch):
    responses = [FakeResponse(401), FakeResponse(200, {"result": {"structuredContent": {"ok": 1}}})]

    class C:
        def post(self, url, headers=None, json=None):
            return responses.pop(0)

    monkeypatch.setattr(tf, "_client", lambda: C())
    assert tf._run("code") == {"ok": 1}
    assert tf._FORCE_REFRESH is False, "the force flag must be consumed, not left armed"


def test_second_401_is_a_real_refusal(feed_env, monkeypatch):
    class C:
        def post(self, url, headers=None, json=None):
            return FakeResponse(401)

    monkeypatch.setattr(tf, "_client", lambda: C())
    with pytest.raises(RuntimeError, match="auth rejected"):
        tf._run("code")


def test_401_without_oauth_is_not_retried(monkeypatch):
    """A station still on the legacy static bearer has nothing to refresh, so a
    401 must stay a single hard failure rather than doubling the load."""
    monkeypatch.setattr(tf, "_SESSION", "sess-1")
    monkeypatch.setattr(tf.cassandra_oauth, "is_enrolled", lambda: False)
    monkeypatch.setattr(tf, "_initialize", lambda: None)
    # The legacy path reads the Keychain, so stub it: this test passed only
    # while a real static bearer still happened to be enrolled on the machine,
    # and started failing the moment a successful --login cleared it.
    monkeypatch.setattr(tf.vault, "get_cassandra_token", lambda: "legacy-static")
    posts = []

    class C:
        def post(self, url, headers=None, json=None):
            posts.append(url)
            return FakeResponse(401)

    monkeypatch.setattr(tf, "_client", lambda: C())
    with pytest.raises(RuntimeError, match="auth rejected"):
        tf._run("code")
    assert len(posts) == 1


# ---------------------------------------------------------------------------
# registration — the shape the provider actually accepts (2026-09-09)
# ---------------------------------------------------------------------------
def test_registration_asks_only_for_grants_the_server_allows(oauth_env, monkeypatch):
    """AuthKit rejects a registration (422) whose grant_types mention the device
    code, even though the server advertises that grant. Pin the accepted set so
    the obvious 'headless station wants device code' edit fails loudly here."""
    seen = {}

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None, json=None):
            seen.update(json or {})
            return FakeResponse(200, {"client_id": "cid-new"})

    meta = {"registration_endpoint": "https://as.example/oauth2/register",
            "token_endpoint": "https://as.example/oauth2/token",
            "authorization_endpoint": "https://as.example/oauth2/authorize",
            "issuer": "https://as.example"}
    blob = co._register(C(), meta, "http://127.0.0.1:8765/callback")
    assert seen["grant_types"] == ["authorization_code", "refresh_token"]
    assert seen["redirect_uris"] == ["http://127.0.0.1:8765/callback"]
    assert seen["token_endpoint_auth_method"] == "none"     # public client, PKCE not a secret
    assert blob["client_id"] == "cid-new"
    assert blob["redirect_uri"] == "http://127.0.0.1:8765/callback"


def test_registration_failure_quotes_the_server(oauth_env):
    """The 422 that cost an afternoon said exactly which fields were wrong; a
    bare exception name threw that away."""
    class C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None, json=None):
            return FakeResponse(422, {"message": "Validation failed", "errors": [
                {"field": "redirect_uris", "code": "each value must be a string"},
                {"field": "grant_types", "code": "must be one of: authorization_code"}]})

    meta = {"registration_endpoint": "https://as.example/oauth2/register",
            "token_endpoint": "https://as.example/oauth2/token"}
    with pytest.raises(co.OAuthError) as e:
        co._register(C(), meta, "http://127.0.0.1:8765/callback")
    assert "redirect_uris" in str(e.value) and "grant_types" in str(e.value)
    assert "422" in str(e.value)


def test_why_handles_every_error_body_shape():
    class NotJson(FakeResponse):
        def json(self):
            raise ValueError("not json")

    gateway_html = NotJson(502)
    gateway_html.text = "<html><title>502 Bad Gateway</title>\nnginx"
    assert "502 Bad Gateway" in co._why(gateway_html)
    assert "\n" not in co._why(gateway_html)               # one line, always

    assert co._why(FakeResponse(500, {})) == "no detail"
    assert co._why(FakeResponse(400, {"error_description": "token is expired"})) \
        == "token is expired"
    assert co._why(FakeResponse(400, {"error": "invalid_grant"})) == "invalid_grant"


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------
def test_pkce_challenge_is_the_sha256_of_the_verifier():
    import base64
    import hashlib
    verifier, challenge = co._pkce()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected
    assert "=" not in challenge and 43 <= len(verifier) <= 128
    assert co._pkce()[0] != verifier          # a fresh verifier every enrolment


def test_login_reuses_a_matching_client_registration(oauth_env, monkeypatch):
    """Re-enrolling on the same port must not register another client — the
    provider keeps every one that is ever created."""
    posts = []

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None, json=None):
            posts.append(url)
            return FakeResponse(200, {"client_id": "cid-new"})

    blob = co._client_blob(C(), redirect_uri="http://127.0.0.1:8765/callback")
    assert blob["client_id"] == "cid-1", "the stored registration should have been reused"
    assert posts == [], "no registration call should have been made"


def test_login_registers_again_when_the_port_moved(oauth_env, monkeypatch):
    """A taken port means an ephemeral one, and a redirect URI the stored client
    was never registered against — that one really does need a new client."""
    monkeypatch.setattr(co, "_discover", lambda c: {
        "registration_endpoint": "https://as.example/oauth2/register",
        "token_endpoint": "https://as.example/oauth2/token",
        "authorization_endpoint": "https://as.example/oauth2/authorize",
        "issuer": "https://as.example"})

    class C:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, data=None, json=None):
            return FakeResponse(200, {"client_id": "cid-new"})

    blob = co._client_blob(C(), redirect_uri="http://127.0.0.1:49512/callback")
    assert blob["client_id"] == "cid-new"
    assert blob["redirect_uri"] == "http://127.0.0.1:49512/callback"


def test_refresh_path_never_cares_about_the_redirect_uri(oauth_env):
    """A scan calls _client_blob with no redirect URI and must accept whatever is
    stored — otherwise a port change at enrolment time would break every scan."""
    stored = dict(CLIENT_BLOB)
    stored["redirect_uri"] = "http://127.0.0.1:49512/callback"
    oauth_env.client = stored

    class C:
        def post(self, *a, **k):
            raise AssertionError("a refresh must never re-register")

    assert co._client_blob(C())["client_id"] == "cid-1"


# ---------------------------------------------------------------------------
# the enrolment, end to end against a real loopback listener
# ---------------------------------------------------------------------------
def _drive_browser(monkeypatch, respond):
    """Stand in for the browser: parse the authorize URL the way a browser would,
    then hit the real redirect URI on a worker thread while login() serves it."""
    import threading
    import urllib.parse
    import urllib.request

    def fake_open(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        target = respond(q)
        if target is None:
            return True

        def go():
            try:
                urllib.request.urlopen(target, timeout=5).read()
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()
        return True

    monkeypatch.setattr(co.webbrowser, "open", fake_open)


@pytest.fixture
def login_env(oauth_env, monkeypatch):
    """A registration that needs no network, and a token endpoint that answers."""
    oauth_env.refresh = None                     # nothing enrolled yet
    monkeypatch.setattr(co, "REDIRECT_PORT", 0)  # ephemeral: never fight a real 8765

    def blob_for(c, redirect_uri=None):
        b = dict(CLIENT_BLOB)
        b["redirect_uri"] = redirect_uri or b["redirect_uri"]
        return b

    monkeypatch.setattr(co, "_client_blob", blob_for)
    monkeypatch.setattr(co, "_client", lambda: _Nullclient())
    return oauth_env


class _Nullclient:
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_login_stores_the_refresh_token(login_env, monkeypatch):
    exchanged = {}

    def fake_exchange(c, blob, form):
        exchanged.update(form)
        return {"access_token": "at-new", "expires_in": 300,
                "refresh_token": "rt-new"}, ""

    monkeypatch.setattr(co, "_token_request", fake_exchange)
    _drive_browser(monkeypatch, lambda q: f"{q['redirect_uri']}?code=the-code&state={q['state']}")

    co.login(timeout_s=10, printer=lambda *a: None)

    assert login_env.refresh == "rt-new"
    assert login_env.access["token"] == "at-new"
    assert login_env.static_cleared, "the dead static bearer must be cleared on success"
    assert exchanged["grant_type"] == "authorization_code"
    assert exchanged["code"] == "the-code"
    assert exchanged["code_verifier"]            # PKCE, not a bare code exchange


def test_login_rejects_a_mismatched_state(login_env, monkeypatch):
    """A code arriving with the wrong state is not our code. Refuse it."""
    monkeypatch.setattr(co, "_token_request",
                        lambda *a: pytest.fail("must not exchange a mismatched code"))
    _drive_browser(monkeypatch, lambda q: f"{q['redirect_uri']}?code=x&state=not-ours")

    with pytest.raises(co.OAuthError, match="state mismatch"):
        co.login(timeout_s=10, printer=lambda *a: None)
    assert login_env.refresh is None


def test_login_surfaces_a_refusal_from_the_authorize_page(login_env, monkeypatch):
    _drive_browser(monkeypatch,
                   lambda q: f"{q['redirect_uri']}?error=access_denied"
                             f"&error_description=user+said+no&state={q['state']}")
    with pytest.raises(co.OAuthError, match="access_denied"):
        co.login(timeout_s=10, printer=lambda *a: None)
    assert login_env.refresh is None


def test_login_without_offline_access_is_not_a_half_enrolment(login_env, monkeypatch):
    """A token response with no refresh token would die in 300s. Refuse to
    record it — a half enrolment makes is_enrolled() lie to every scan."""
    monkeypatch.setattr(co, "_token_request",
                        lambda c, b, f: ({"access_token": "at", "expires_in": 300}, ""))
    _drive_browser(monkeypatch, lambda q: f"{q['redirect_uri']}?code=c&state={q['state']}")

    with pytest.raises(co.OAuthError, match="no refresh token"):
        co.login(timeout_s=10, printer=lambda *a: None)
    assert login_env.refresh is None and login_env.client is None


def test_login_ignores_a_stray_favicon_request(login_env, monkeypatch):
    """Browsers ask for more than the redirect. A stray GET must not consume the
    wait and leave the enrolment timing out on a page that says it worked."""
    import threading
    import urllib.parse
    import urllib.request

    monkeypatch.setattr(co, "_token_request",
                        lambda c, b, f: ({"access_token": "at", "expires_in": 300,
                                          "refresh_token": "rt-new"}, ""))

    def fake_open(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        root = q["redirect_uri"].rsplit("/", 1)[0]

        def go():
            for target in (f"{root}/favicon.ico",
                           f"{q['redirect_uri']}?code=c&state={q['state']}"):
                try:
                    urllib.request.urlopen(target, timeout=5).read()
                except Exception:
                    pass
        threading.Thread(target=go, daemon=True).start()
        return True

    monkeypatch.setattr(co.webbrowser, "open", fake_open)
    co.login(timeout_s=10, printer=lambda *a: None)
    assert login_env.refresh == "rt-new"


# ---------------------------------------------------------------------------
# security properties (audited 2026-09-09) — each one measured, not assumed
# ---------------------------------------------------------------------------
def test_transport_does_not_follow_redirects():
    """The refresh token rides in a POST BODY, and httpx strips only the
    Authorization HEADER across hosts. A followed 307 from the token endpoint
    hands the credential to whatever Location names, http:// included — measured
    against a local server pair before this was turned off."""
    with co._client() as c:
        assert c.follow_redirects is False


@pytest.mark.parametrize("bad", [
    "http://as.example/oauth2/token",
    "ftp://as.example/token",
    "//as.example/token",
])
def test_https_guard_refuses_cleartext(bad):
    with pytest.raises(co.OAuthError, match="non-https"):
        co._https(bad, "token endpoint")


def test_stored_blob_with_cleartext_token_endpoint_is_refused(oauth_env, monkeypatch):
    """The guard must bite on a STORED registration too, not only on a freshly
    discovered one — otherwise an enrolment made before the guard existed keeps
    posting the refresh token in the clear forever."""
    bad = dict(CLIENT_BLOB)
    bad["token_endpoint"] = "http://as.example/oauth2/token"
    oauth_env.client = bad

    class C:
        def post(self, *a, **k):
            raise AssertionError("must not reach the network with a cleartext endpoint")

    with pytest.raises(co.OAuthError, match="non-https"):
        co._token_request(C(), bad, {"grant_type": "refresh_token", "refresh_token": "rt"})


def test_discovery_refuses_a_cleartext_authorization_server(oauth_env, monkeypatch):
    """Discovery input arrives from the network and is exactly what an attacker
    would rewrite to harvest a refresh token."""
    monkeypatch.setattr(co, "_resource_metadata_url", lambda c: "https://rs.example/meta")
    _fake_http(monkeypatch, [
        (None, FakeResponse(200, {"authorization_servers": ["http://evil.example"]})),
    ])
    with co._client() as c:
        with pytest.raises(co.OAuthError, match="non-https"):
            co._discover(c)


def test_manual_login_also_validates_state(login_env, monkeypatch):
    """--manual is where a pasted URL can come from somewhere else, so it is the
    mode that needs the state check most. It was briefly exempt."""
    monkeypatch.setattr(co, "_token_request",
                        lambda *a: pytest.fail("must not exchange a mismatched code"))
    monkeypatch.setattr("builtins.input",
                        lambda *a: "http://127.0.0.1:8765/callback?code=x&state=attacker")
    with pytest.raises(co.OAuthError, match="state mismatch"):
        co.login(timeout_s=5, manual=True, printer=lambda *a: None)
    assert login_env.refresh is None
