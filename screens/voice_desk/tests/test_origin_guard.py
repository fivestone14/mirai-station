"""The Origin guard — the sidecar's only defense against the open web.

WebSockets are exempt from CORS, so "binds 0.0.0.0, LAN is the perimeter" is
false by default: any page the user visits in any browser can open a socket to
this port and drive the agent on their Claude subscription, reading the SNDK
scene back out as it goes. Browsers always attach Origin and cannot forge it,
so refusing unrecognized origins is the whole fix.

The allowlist is by shape rather than a fixed address so the tablet survives a
new DHCP lease; these tests pin both directions of that trade."""
import pytest

import voice_server as V


@pytest.mark.parametrize("origin", [
    "http://localhost:8787",
    "http://127.0.0.1:8787",
    "http://192.168.1.40:8787",     # LAN
    "http://10.0.0.7:8787",         # LAN, other RFC1918 block
    "http://172.16.4.9:8787",       # LAN, the 172.16/12 block
    "http://mirai-station.local:8787",
    "https://box.tailnet-name.ts.net",
    "https://100.101.102.103",      # Tailscale CGNAT — NOT is_private in py3.12
])
def test_lan_and_tailnet_pages_are_allowed(origin):
    assert V._origin_ok(origin) is True


@pytest.mark.parametrize("origin", [
    "https://evil.com",
    "http://evil.com:8788",         # matching port earns nothing
    "https://8.8.8.8",
    "null",                         # sandboxed iframe / opaque origin
    "https://localhost.evil.com",   # suffix spoof
    "https://sub.ts.net.evil.com",  # ts.net suffix spoof
])
def test_web_pages_are_refused(origin):
    assert V._origin_ok(origin) is False


def test_non_browser_clients_pass():
    """repl.py and bench.py send no Origin at all; they are not the threat and
    locking them out would break the local dev loop."""
    assert V._origin_ok(None) is True


def test_extra_origins_env_is_honored(monkeypatch):
    monkeypatch.setattr(V, "_EXTRA_ORIGINS", {"https://mirai.example.net"})
    assert V._origin_ok("https://mirai.example.net") is True
    assert V._origin_ok("https://other.example.net") is False


def test_request_origin_reads_both_websockets_api_shapes():
    """websockets moved the handshake headers from ws.request_headers (legacy)
    to ws.request.headers (asyncio server); the guard must read whichever is
    present, because a missing Origin is treated as a trusted non-browser
    client — so reading the wrong attribute would fail open."""
    class _New:
        class request:
            headers = {"Origin": "https://evil.com"}

    class _Legacy:
        request_headers = {"Origin": "https://evil.com"}

    assert V._request_origin(_New()) == "https://evil.com"
    assert V._request_origin(_Legacy()) == "https://evil.com"


def test_unlocatable_headers_fail_closed():
    """The dangerous case is not "no Origin" but "cannot find the headers at
    all": a websockets release that moves them again would downgrade every
    browser to trusted-non-browser and silently undo this guard."""
    assert V._request_origin(object()) is V._ORIGIN_UNKNOWN
    assert V._origin_ok(V._ORIGIN_UNKNOWN) is False


def test_ipv4_mapped_ipv6_is_judged_by_the_embedded_address():
    """::ffff:8.8.8.8 is a public address wearing a v6 prefix; some Python
    versions call the mapped form private on the prefix alone."""
    assert V._origin_ok("http://[::ffff:8.8.8.8]:8787") is False
    assert V._origin_ok("http://[::ffff:192.168.1.40]:8787") is True
    assert V._origin_ok("http://[::1]:8787") is True
