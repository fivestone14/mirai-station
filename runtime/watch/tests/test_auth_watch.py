"""Unit tests for the Schwab login keep-alive (token watcher + re-auth + ntfy).

Pure-logic, no network, no Schwab connection:

    cd runtime && python3 -m unittest watch.tests.test_auth_watch -v

These pin the behaviour that actually matters: the day-6 warn / day-7 expired
boundaries, fail-safe on a missing timestamp, the login URL shape (no secrets
beyond the App Key), and that the ntfy request carries the tap-to-open Click
header.
"""
from __future__ import annotations

import unittest
import urllib.parse

from watch.intraday import auth_check, push_ntfy, reauth, settings, token_watcher

DAY = 86400.0


class TestTokenAge(unittest.TestCase):
    def test_creation_timestamp_from_schwab_wrapper(self):
        tok = {"creation_timestamp": 1000, "token": {"refresh_token": "x"}}
        self.assertEqual(token_watcher.creation_timestamp_from_token(tok), 1000)

    def test_creation_timestamp_nested_fallback(self):
        tok = {"token": {"creation_timestamp": 1234}}
        self.assertEqual(token_watcher.creation_timestamp_from_token(tok), 1234)

    def test_creation_timestamp_missing_is_none(self):
        self.assertIsNone(token_watcher.creation_timestamp_from_token({"token": {}}))
        self.assertIsNone(token_watcher.creation_timestamp_from_token(None))

    def test_age_days(self):
        now = 100 * DAY
        self.assertAlmostEqual(token_watcher.token_age_days(98 * DAY, now=now), 2.0, places=6)

    def test_age_none_without_timestamp(self):
        self.assertIsNone(token_watcher.token_age_days(None))


class TestClassify(unittest.TestCase):
    def _at(self, age_days):
        # creation = now - age; fix now at a round number for determinism
        now = 1000 * DAY
        return token_watcher.classify(now - age_days * DAY, now=now)

    def test_fresh_is_ok(self):
        self.assertEqual(self._at(1.0).state, "ok")

    def test_day6_warns(self):
        # warn_after_days default is 6
        self.assertEqual(self._at(6.0).state, "warn")
        self.assertEqual(self._at(6.5).state, "warn")

    def test_just_before_warn_is_ok(self):
        self.assertEqual(self._at(5.9).state, "ok")

    def test_day7_expired(self):
        self.assertEqual(self._at(7.0).state, "expired")
        self.assertEqual(self._at(9.0).state, "expired")

    def test_unknown_when_no_timestamp(self):
        self.assertEqual(token_watcher.classify(None).state, "unknown")

    def test_days_left_sign(self):
        self.assertGreater(self._at(2.0).days_left, 0)     # time remaining
        self.assertLess(self._at(8.0).days_left, 0)        # past the wall

    def test_needs_reauth_matrix(self):
        self.assertFalse(token_watcher.needs_reauth(self._at(1.0)))   # ok
        self.assertTrue(token_watcher.needs_reauth(self._at(6.0)))    # warn
        self.assertTrue(token_watcher.needs_reauth(self._at(8.0)))    # expired
        self.assertTrue(token_watcher.needs_reauth(token_watcher.classify(None)))  # unknown (fail-safe)


class TestReauthUrl(unittest.TestCase):
    def test_authorize_url_shape(self):
        url = reauth.build_authorize_url("APPKEY123", "https://127.0.0.1:8182")
        self.assertTrue(url.startswith("https://api.schwabapi.com/v1/oauth/authorize?"))
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["client_id"], ["APPKEY123"])
        self.assertEqual(q["redirect_uri"], ["https://127.0.0.1:8182"])
        self.assertEqual(q["response_type"], ["code"])

    def test_url_encodes_callback(self):
        url = reauth.build_authorize_url("k", "https://example.com/cb?x=1")
        # the callback's own query must be percent-encoded inside the param
        self.assertIn("redirect_uri=https%3A%2F%2Fexample.com", url)


class TestReauthPing(unittest.TestCase):
    def _status(self, state, days_left=1.0):
        return token_watcher.AuthStatus(state=state, age_days=6.0, days_left=days_left, reason="r")

    def test_expired_is_urgent(self):
        ping = reauth.build_reauth_ping(self._status("expired", -0.2), "https://login")
        self.assertEqual(ping.priority, "urgent")
        self.assertEqual(ping.click_url, "https://login")

    def test_warn_is_high(self):
        ping = reauth.build_reauth_ping(self._status("warn", 0.8), "https://login")
        self.assertEqual(ping.priority, "high")

    def test_message_changes_without_url(self):
        with_url = reauth.build_reauth_ping(self._status("warn"), "https://login").message
        without = reauth.build_reauth_ping(self._status("warn"), None).message
        self.assertIn("Tap to log in", with_url)
        self.assertIn("mini", without)


class TestNtfyRequest(unittest.TestCase):
    def test_build_request_url_and_body(self):
        req = push_ntfy.build_request("hello", topic="my-topic", server="https://ntfy.sh")
        self.assertEqual(req.full_url, "https://ntfy.sh/my-topic")
        self.assertEqual(req.data, b"hello")
        self.assertEqual(req.get_method(), "POST")

    def test_click_header_carries_oauth_link(self):
        req = push_ntfy.build_request("x", topic="t", click_url="https://login", priority="high")
        # urllib capitalizes header keys
        self.assertEqual(req.headers.get("Click"), "https://login")
        self.assertEqual(req.headers.get("Priority"), "high")

    def test_title_header_is_ascii_safe(self):
        # an emoji title must not raise when set as an HTTP header
        req = push_ntfy.build_request("x", topic="t", title="🔑 Schwab login expired")
        self.assertNotIn("🔑", req.headers.get("Title", ""))
        self.assertIn("Schwab login expired", req.headers.get("Title", ""))

    def test_server_trailing_slash_normalized(self):
        req = push_ntfy.build_request("x", topic="t", server="https://ntfy.sh/")
        self.assertEqual(req.full_url, "https://ntfy.sh/t")

    def test_send_noops_without_topic(self):
        # Stub the topic empty — the deployed config has a real one, and this
        # test must never POST to the live channel.
        from unittest import mock
        with mock.patch.object(settings, "ntfy_topic", return_value=""):
            rec = push_ntfy.send("hi")
        self.assertFalse(rec["dispatched"])
        self.assertIn("no ntfy topic", rec["reason"])


class TestCassandraTokenHealth(unittest.TestCase):
    """The native-GEX data-token probe decision + ping (pure, no network)."""

    def test_needs_ping_only_on_auth_rejected(self):
        self.assertTrue(auth_check.cassandra_needs_ping("auth_rejected"))
        self.assertFalse(auth_check.cassandra_needs_ping("ok"))
        self.assertFalse(auth_check.cassandra_needs_ping("unknown"))  # no crying wolf on transient

    def test_dead_token_ping_is_urgent_and_actionable(self):
        cp = auth_check.build_cassandra_ping("auth_rejected", "native_gex_feed: auth rejected (401)")
        self.assertEqual(cp.priority, "urgent")
        self.assertIn("proxy", cp.message.lower())     # says what degraded
        self.assertIn("--setup", cp.message)           # says how to fix it
        self.assertIn("401", cp.message)

    def test_ok_probe_is_informational_not_urgent(self):
        cp = auth_check.build_cassandra_ping("ok", "authenticated")
        self.assertEqual(cp.priority, "default")


class TestSettingsAuth(unittest.TestCase):
    def test_auth_accessors(self):
        self.assertEqual(settings.auth_warn_after_days(), 6)
        self.assertEqual(settings.auth_hard_limit_days(), 7)

    def test_ntfy_env_override(self):
        import os
        os.environ["MIRAI_NTFY_TOPIC"] = "env-topic-xyz"
        try:
            settings.reload()
            self.assertEqual(settings.ntfy_topic(), "env-topic-xyz")
        finally:
            del os.environ["MIRAI_NTFY_TOPIC"]
            settings.reload()


if __name__ == "__main__":
    unittest.main()
