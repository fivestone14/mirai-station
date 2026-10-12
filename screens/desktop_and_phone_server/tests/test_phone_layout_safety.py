"""Layout invariants the render harness is structurally blind to: what the
stylesheet and the shell decide about the phone's HEIGHT, which every headless
check gets wrong the same way.

Inside the app every viewport unit reads ZERO. Measured on a Galaxy S20+
(Chrome 152 WebView): 100vh = 0 and 100dvh = 0 against a visible height of
779px, because Android WebView reports a zero-height viewport to a page when
the view is sized wrap-content, and the shell's SwipeRefreshLayout gave it
that by default. The explainer sheet was max-height:84dvh and opened as a
32px strip; a min-height:100dvh has never done anything on a phone. Every
desktop check passed, because desktop browsers report the real height. So the
phone pages read one measured number instead, and these pin that no viewport
unit can creep back in, that the number is measured before anything paints,
and that the shell gives the WebView a height to measure.

What a page's script shows and hides is the other thing a harness cannot see:
the hidden attribute loses to any author rule that sets a display, so the
last test reads the hides off the script and the displays off the stylesheet.
"""
import re
from pathlib import Path

M = Path(__file__).resolve().parents[1] / "static" / "m"
SPX = (M / "jev-spx.html").read_text()

_UNIT = re.compile(r"(?<![\w-])\d*\.?\d+(?:d|s|l)?v(?:h|w|min|max|i|b)\b")


def _css_code(html):
    """Every stylesheet in the page with its comments removed, because the
    comments quote the retired units on purpose."""
    css = "\n".join(re.findall(r"(?s)<style>(.*?)</style>", html))
    return re.sub(r"(?s)/\*.*?\*/", "", css)


def _flat_rules(css):
    """(selector, [declaration, ...]) for every rule with no rule nested in it,
    each declaration normalised to "property:value"."""
    out = []
    for sel, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        decls = []
        for d in block.split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decls.append(k.strip() + ":" + " ".join(v.split()))
        out.append((" ".join(sel.split()), decls))
    return out


def test_no_phone_page_reads_a_viewport_unit():
    css = _css_code(SPX)
    found = _UNIT.findall(css)
    # the ONE permitted use is the fallback value of the token itself
    assert found == ["100dvh"], f"jev-spx.html reads a viewport unit: {found}"
    assert re.search(r"--app-h:100dvh;", css), "jev-spx.html lost the token"
    assert "var(--app-h)" in css, "jev-spx.html declares the token and never reads it"
    # the SPX reads (2026-10-09) has no sheet, so no height token, and the retired SNDK forecast (2026-10-10) is a
    # redirect with no stylesheet: neither reads a viewport unit at all
    for name in ("reads.html", "jev.html"):
        assert _UNIT.findall(_css_code((M / name).read_text())) == [], name


def _measure_script(html):
    m = re.search(r"(?s)<body>\s*(<script>.*?</script>)", html)
    return m.group(1) if m else None


def test_the_forecast_page_measures_the_height_before_anything_paints():
    """The script is the first thing in <body> on the page with a height token
    (the forecast, whose sheets are capped on it), so the token is set before
    the first paint reads it."""
    script = _measure_script(SPX)
    assert script, "the page has lost the script at the top of <body>"
    for need in ("window.innerHeight", "setProperty('--app-h'", "'resize'",
                 "'orientationchange'", "visualViewport"):
        assert need in script, need


def test_the_shell_gives_the_webview_a_real_height():
    """The cause, not the symptom: WRAP_CONTENT is what made the WebView report
    a zero-height viewport. Android's WebView guidance is match_parent."""
    kt = (Path(__file__).resolve().parents[3] / "screens" / "android_phone_app" / "app" / "src" / "main"
          / "java" / "com" / "mirai" / "mobile" / "MainActivity.kt").read_text()
    code = "\n".join(l.split("//")[0] for l in kt.splitlines())
    assert re.search(r"addView\(web,\s*ViewGroup\.LayoutParams\(\s*ViewGroup\.LayoutParams\.MATCH_PARENT,"
                     r"\s*ViewGroup\.LayoutParams\.MATCH_PARENT\)\)", code), \
        "the WebView is added without MATCH_PARENT params"
    assert "addView(web)" not in code


def _hidden_by_script(html, js):
    """{id: [class, ...]} for every element the page's script shows and hides
    with the hidden attribute, directly or through a const it holds it in."""
    held = dict(re.findall(r"const (\w+) = \$\('(\w+)'\)", js))
    ids = set(re.findall(r"\$\('(\w+)'\)\.hidden\s*=", js))
    ids |= {held[v] for v in re.findall(r"\b(\w+)\.hidden\s*=", js) if v in held}
    out = {}
    for el in ids:
        tag = re.search(r'<[a-z]+\b[^>]*\bid="%s"[^>]*>' % el, html)
        assert tag, f"#{el} is hidden by the script and not in the markup"
        cls = re.search(r'\bclass="([^"]*)"', tag.group(0))
        out[el] = cls.group(1).split() if cls else []
    return out


def test_what_the_script_hides_stays_hidden():
    """The page shows and hides parts of itself with the hidden attribute, and
    it hides only through the browser's own [hidden]{display:none}, which any
    author rule that sets a display beats. A change pill (inline-flex) and a
    last-scan line (block) did, measured in WebKit: once shown, each kept its
    last figure on screen after the script hid it. So every element the page's
    script hides, whose own rule sets a display, carries a [hidden] rule that
    takes it back to none."""
    js = "\n".join(re.findall(r"(?s)<script>(.*?)</script>", SPX))
    hidden = _hidden_by_script(SPX, js)
    assert {"load", "poll"} <= set(hidden), "the hides are no longer where this test reads them"
    rules = [(part.strip(), decls) for sel, decls in _flat_rules(_css_code(SPX))
             for part in sel.split(",")]
    for el, classes in sorted(hidden.items()):
        for c in classes:
            shown = [d for s, decls in rules if s == "." + c
                     for d in decls if d.startswith("display:") and d != "display:none"]
            if shown:
                assert any(s == f".{c}[hidden]" and "display:none" in decls for s, decls in rules), \
                    f"#{el} sets {shown[0]} on .{c}, which beats hidden"


def test_the_body_reserves_the_tab_bar():
    """--tab-h once shipped at 64px against a bar that renders 74, so the body
    reserved ten pixels too few for a position:fixed bar and the footer drew
    five pixels underneath it. The pages now set the constant rather than
    measure the bar, so the constant is pinned at the bar's rendered height,
    and the body is pinned to reserve it."""
    for name in ("jev-spx.html", "reads.html"):
        css = _css_code((M / name).read_text())
        m = re.search(r"--tab-h:\s*(\d+)px", css)
        assert m, f"{name}: --tab-h has no value"
        assert int(m.group(1)) >= 74, f"{name}: --tab-h under-reserves and would clip the footer"
        body = next(decls for sel, decls in _flat_rules(css) if sel == "body")
        pad = [d for d in body if d.startswith("padding:")]
        assert pad and "var(--tab-h)" in pad[0], f"{name}: the body does not reserve the bar"
