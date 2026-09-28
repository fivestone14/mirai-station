"""
One access-token renewal at a time across every client over the token file.

Hermetic: a fake keyring, a token file under tmp_path, and an httpx
MockTransport standing in for Schwab, so nothing here reaches the network, the
real Keychain or the real token file.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("schwab")

import httpx  # noqa: E402

from .test_vault import FakeKeyring  # noqa: E402

TOKEN_ENDPOINT = "https://api.schwabapi.com/v1/oauth/token"


@pytest.fixture
def station(monkeypatch, tmp_path):
    """vault + iv_fetcher over a tmp token file, a fake keyring and a fake Schwab
    that counts renewals and answers each with a numbered access token."""
    import importlib
    import keyring
    import vault
    import iv_fetcher

    importlib.reload(vault)
    monkeypatch.setattr(vault, "TOKEN_FILE", tmp_path / ".schwab_token.json.enc")
    monkeypatch.setattr(vault, "LOCK_FILE", tmp_path / ".schwab_token.lock")
    monkeypatch.setattr(vault, "RENEW_LOCK_FILE", tmp_path / ".schwab_token.renew.lock")
    fake = FakeKeyring()
    monkeypatch.setattr(keyring, "get_password", fake.get_password)
    monkeypatch.setattr(keyring, "set_password", fake.set_password)
    vault.store_credentials("key", "secret", "https://127.0.0.1:8182")

    class FakeSchwab:
        def __init__(self):
            self.renewals = 0
            self.renew_delay_s = 0.0
            self.on_renew = None
            self.bearers: list[str] = []

        def handle(self, request):
            if str(request.url) == TOKEN_ENDPOINT:
                if self.on_renew:
                    self.on_renew()
                time.sleep(self.renew_delay_s)
                self.renewals += 1
                return httpx.Response(200, json={
                    "access_token": f"renewed-{self.renewals}", "refresh_token": "refresh",
                    "token_type": "Bearer", "expires_in": 1800, "scope": "api"})
            self.bearers.append(request.headers["Authorization"])
            return httpx.Response(200, json={})

    schwab = FakeSchwab()

    def save(access_token, expires_in_s):
        vault.save_token({"creation_timestamp": int(time.time()) - 86400, "token": {
            "access_token": access_token, "refresh_token": "refresh", "token_type": "Bearer",
            "expires_in": 1800, "expires_at": int(time.time()) + expires_in_s}})

    def build():
        client = iv_fetcher._build_client()
        client.session._transport = httpx.MockTransport(schwab.handle)
        return client

    return vault, schwab, save, build


def test_a_token_renewed_by_another_job_is_used_not_renewed_again(station):
    vault, schwab, save, build = station
    save("old", expires_in_s=60)            # inside the 300 s leeway: due
    client = build()
    save("renewed-elsewhere", expires_in_s=1790)

    client.get_quote("SPY")

    assert schwab.renewals == 0
    assert schwab.bearers == ["Bearer renewed-elsewhere"]
    assert client.token_metadata.token["access_token"] == "renewed-elsewhere"


def test_two_jobs_due_together_renew_once(station):
    vault, schwab, save, build = station
    save("old", expires_in_s=60)
    first, second = build(), build()

    first.get_quote("SPY")
    second.get_quote("SPY")

    assert schwab.renewals == 1
    assert schwab.bearers == ["Bearer renewed-1", "Bearer renewed-1"]
    assert vault.load_token()["token"]["access_token"] == "renewed-1"


def test_jobs_renewing_at_the_same_moment_wait_on_the_lock(station):
    vault, schwab, save, build = station
    save("old", expires_in_s=60)
    schwab.renew_delay_s = 0.3
    clients = [build() for _ in range(4)]
    threads = [threading.Thread(target=c.get_quote, args=("SPY",)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert schwab.renewals == 1
    assert schwab.bearers == ["Bearer renewed-1"] * 4


def test_the_renewal_is_made_under_the_lock(station):
    vault, schwab, save, build = station
    save("old", expires_in_s=60)
    held = []

    def try_lock():
        lock = vault.renewal_lock()
        lock.timeout = 0
        try:
            lock.acquire()
        except Exception as e:              # filelock.Timeout
            held.append(type(e).__name__)
        else:
            lock.release()
            held.append("free")

    schwab.on_renew = try_lock
    build().get_quote("SPY")

    assert held == ["Timeout"]


def test_a_fresh_token_never_touches_the_lock_or_the_endpoint(station, monkeypatch):
    vault, schwab, save, build = station
    save("fresh", expires_in_s=1700)
    client = build()
    monkeypatch.setattr(vault, "renewal_lock", lambda: pytest.fail("lock taken for a fresh token"))

    client.get_quote("SPY")

    assert schwab.renewals == 0
    assert schwab.bearers == ["Bearer fresh"]


def test_a_failed_renewal_still_raises_and_saves_nothing(station):
    vault, schwab, save, build = station
    save("old", expires_in_s=60)
    client = build()
    client.session._transport = httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": "invalid_request",
                                                  "error_description": "Bad Request"}))
    from authlib.integrations.base_client import OAuthError

    with pytest.raises(OAuthError):
        client.get_quote("SPY")
    assert vault.load_token()["token"]["access_token"] == "old"
