"""The Host guard — what keeps "LAN-only" from being merely aspirational.

The server binds 0.0.0.0 with no auth, so the only thing separating "the tablet
can read the diary" from "any web page can read the diary" is that a rebound
attacker domain still names itself in the Host header. These tests pin the two
halves that matter: every way the tablet legitimately addresses this box keeps
working (including after a new DHCP lease, which is why the rule is by shape and
not a fixed address), and every attacker-controlled name is refused.

_host_ok only ever touches self.headers, so a dict stand-in exercises it without
standing up a socket."""
import pytest

import server


class _Req:
    """Minimal stand-in for BaseHTTPRequestHandler — headers and the request
    version are all the guards read."""

    def __init__(self, host=None, version="HTTP/1.1", **headers):
        self.headers = dict(headers)
        if host is not None:
            self.headers["Host"] = host
        self.request_version = version


def _ok(host, version="HTTP/1.1"):
    return server.Handler._host_ok(_Req(host, version))


@pytest.mark.parametrize("host", [
    "localhost:8787",
    "127.0.0.1:8787",
    "[::1]:8787",
    "192.168.1.40:8787",      # LAN
    "10.0.0.7:8787",          # LAN, other RFC1918 block
    "172.16.4.9:8787",        # LAN, the 172.16/12 block
    "mirai-station.local:8787",
    "box.tailnet-name.ts.net",
    "100.101.102.103",        # Tailscale CGNAT — NOT is_private in py3.12
])
def test_local_and_lan_names_are_allowed(host):
    assert _ok(host) is True


@pytest.mark.parametrize("host", [
    "evil.com",
    "evil.com:8787",          # matching port earns nothing
    "8.8.8.8",                # public IP
    "localhost.evil.com",     # suffix spoof
    "sub.ts.net.evil.com",    # ts.net suffix spoof
    "mirai-station.local.evil.com",
])
def test_attacker_controlled_names_are_refused(host):
    assert _ok(host) is False


def test_absent_host_is_refused_on_http_1_1():
    """HTTP/1.1 makes Host mandatory, so an absent one is a raw-socket caller.
    Refusing it also closes the absolute-form request line
    (GET http://evil.com/... HTTP/1.1), which carries no Host at all and would
    otherwise skip this check entirely."""
    assert _ok(None, "HTTP/1.1") is False


def test_absent_host_still_allowed_for_http_1_0():
    """Pre-1.1 clients legitimately omit Host, and no browser speaks 1.0."""
    assert _ok(None, "HTTP/1.0") is True


def test_extra_hosts_env_is_honored(monkeypatch):
    """A public name in front of a reverse proxy is a legitimate deployment —
    it just has to be named explicitly rather than inferred."""
    monkeypatch.setattr(server, "_EXTRA_HOSTS", {"mirai.example.net"})
    assert _ok("mirai.example.net") is True
    assert _ok("other.example.net") is False


# ---------------------------------------------------------------------------
# CSRF — the write route. _host_ok structurally CANNOT cover this: a page on
# evil.com fetching http://127.0.0.1:8787/... makes the browser send
# Host: 127.0.0.1:8787, so the Host check passes every time. Origin is what
# actually names the caller.
# ---------------------------------------------------------------------------

def test_the_server_has_no_write_path_at_all():
    """08-23: the station went public and the reasoning switch — its one write — went with
    it. A visitor must not be able to silence the model. The guard that protected that POST
    (Origin / Sec-Fetch-Site) went too, because a read-only server does not need one; if a
    write ever returns here, BOTH have to come back with it, which is what this pins."""
    assert not hasattr(server.Handler, "do_POST")
    assert not hasattr(server.Handler, "_same_site_ok")
    assert not hasattr(server, "_CONTROL_PATH")


def test_ipv4_mapped_ipv6_is_judged_by_the_embedded_address():
    """[::ffff:8.8.8.8] is a public address wearing a v6 prefix; some Python
    versions call the mapped form private on the strength of the prefix."""
    assert _ok("[::ffff:8.8.8.8]:8787") is False
    assert _ok("[::ffff:192.168.1.40]:8787") is True
    assert _ok("[::1]:8787") is True


# ---------------------------------------------------------------------------
# The raw explorer's readable set. The suffix allowlist used to live only on
# the directory listing — but the listing is a convenience and the fetch is the
# actual permission, so anything the fetch skipped was readable by name.
# ---------------------------------------------------------------------------

def test_raw_fetch_enforces_the_suffix_allowlist(tmp_path, monkeypatch):
    (tmp_path / "ok.json").write_text('{"a": 1}')
    (tmp_path / "secret.enc").write_bytes(b"\x00binary")
    (tmp_path / "notes.pem").write_text("-----BEGIN PRIVATE KEY-----")
    monkeypatch.setattr(server, "RAW_ROOTS", {"state": tmp_path})

    assert server._raw_file("state", "ok.json", 50).get("kind") == "json"
    assert server._raw_file("state", "secret.enc", 50) == {"error": "not found"}
    assert server._raw_file("state", "notes.pem", 50) == {"error": "not found"}


def test_voice_transcripts_are_never_served(tmp_path, monkeypatch):
    """Verbatim recordings of the operator talking, and a live agent session
    id, are a different category of private than a diary row."""
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "convo-2026-08-06.jsonl").write_text('{"said": "..."}\n')
    (tmp_path / "voice" / "session.json").write_text('{"session_id": "abc"}')
    (tmp_path / "reversion").mkdir()
    (tmp_path / "reversion" / "2026-08-06.jsonl").write_text('{"row": 1}\n')
    monkeypatch.setattr(server, "RAW_ROOTS", {"state": tmp_path})

    assert server._raw_file("state", "voice/convo-2026-08-06.jsonl", 50) == {"error": "not found"}
    assert server._raw_file("state", "voice/session.json", 50) == {"error": "not found"}
    # the ordinary diary still reads, or the explorer would be pointless
    assert server._raw_file("state", "reversion/2026-08-06.jsonl", 50).get("kind") == "jsonl"

    listed = [i["rel"] for i in server._raw_index()["state"]]
    assert not any(r.startswith("voice") for r in listed)
    assert "reversion/2026-08-06.jsonl" in listed

def test_the_handler_keeps_every_method_do_get_needs():
    """08-23, and the reason this test exists: a cut aimed at _same_site_ok took _send_json
    with it, because the two were adjacent. Every /api route then raised AttributeError, and
    do_GET's own error path called that same missing method — so the connection HUNG instead
    of answering 500, and `/` kept working because static files go through _send_file. It
    looked healthy from the browser. Pin the handler's surface so a removal cannot quietly
    take a neighbour."""
    for m in ("do_GET", "_send_json", "_send_file", "_deny", "_host_ok"):
        assert callable(getattr(server.Handler, m, None)), f"Handler.{m} is gone"
