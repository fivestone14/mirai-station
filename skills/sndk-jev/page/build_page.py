"""Build the JEV decision service page for SNDK PRO.

    python3 build_page.py OUT.html

Reads, all read-only:
  skills/sndk-jev/spec/labels.json         the label spec
  skills/sndk-jev/questions/sndk_pro.json  the questions, with viewpoint, status, ask and why per question
  skills/sndk-jev/questions/sndk_hour.json the two sums
  state/jev/                               weights, cadence, the newest sum record, the weight log
The cuts and steps quoted in the prose come from the sndk_jev package, never typed here.
"""
from __future__ import annotations

import html
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

SKILL = Path.home() / ".claude/plugins/mirai-station/skills/sndk-jev"
STATE = Path.home() / ".claude/plugins/mirai-station/state"
sys.path.insert(0, str(SKILL))
from sndk_jev.cadence import CHANGE_CUT, MIN_READS, STEPS, parse_cadence  # noqa: E402
from sndk_jev.grade import BAR_GAP_MAX_MIN, CLOSE_GRACE_MIN, MIN_GRADED  # noqa: E402
from sndk_jev.hour import HOUR_QIDS, MIN_WEIGHT  # noqa: E402

if len(sys.argv) != 2:
    raise SystemExit("usage: python3 build_page.py OUT.html")
OUT = Path(sys.argv[1])


def load_json(path: Path, role: str, required: bool = True):
    """The file as JSON, or exit naming the path: a required file must exist, any file present must parse."""
    if not path.is_file():
        if required:
            raise SystemExit(f"{role} missing: {path}")
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise SystemExit(f"{role} is empty: {path}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{role} is not valid JSON: {path} ({e})")


def _jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


SPEC = load_json(SKILL / "spec" / "labels.json", "the label spec")
QDOC = load_json(SKILL / "questions" / "sndk_pro.json", "the questions")
E = lambda s: html.escape(str(s), quote=True)
PATH_RE = re.compile(r"`([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)`")

# ------------------------------------------------------------------ data

def all_questions():
    out = []
    for g in QDOC["groups"]:
        for qid, q in g["questions"].items():
            out.append((g["id"], qid, q))
    return out


QS = all_questions()
N_Q = len(QS)
N_G = len(QDOC["groups"])
VP_ORDER = [v["id"] for v in SPEC["viewpoints"]] + ["outcome"]
VP_TITLE = {v["id"]: v["title"] for v in SPEC["viewpoints"]}
VP_TITLE["outcome"] = "The outcome itself, shadow only"
VP_SHORT = {"strikes": "strikes", "volume_price": "vol & price", "history": "yesterday", "indicators": "indicators",
            "space_time": "space & time", "framing": "framing", "news": "news", "outcome": "outcome"}
STATUS_COUNT = Counter(l["status"] for l in SPEC["labels"])

reads_of: dict[str, list[str]] = {}
for gid, qid, q in QS:
    for p in dict.fromkeys(PATH_RE.findall(json.dumps(q, ensure_ascii=False))):
        reads_of.setdefault(p, []).append(qid)


def read_by(path: str) -> list[str]:
    group = path.split(".")[0]
    return list(dict.fromkeys(reads_of.get(path, []) + reads_of.get(group, [])))


def q_status(q) -> str:
    return q["status"]


def q_viewpoint(gid, q) -> str:
    return q.get("viewpoint") or ("outcome" if gid == "outcome_shadow" else gid)


n_live = sum(1 for _, _, q in QS if q_status(q) == "live")
n_shadow = sum(1 for _, _, q in QS if q_status(q) == "shadow")
n_dark = sum(1 for _, _, q in QS if q_status(q) == "dark")
N_Q_SENT = N_Q - n_dark
N_G_SENT = sum(1 for g in QDOC["groups"] if any(q_status(q) != "dark" for q in g["questions"].values()))

# ------------------------------------------------------------------ css

CSS = r"""
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Schibsted+Grotesk:wght@400;500;600;700&display=swap');
:root{
  --ground:#edf0f4;--surface:#ffffff;--surface-2:#f5f7fa;--line:#d9dfe7;--line-strong:#c3ccd8;
  --ink:#121923;--ink-2:#44505f;--ink-3:#5f6b7c;--accent:#1f4a85;--accent-soft:#e3ebf7;--code-bg:#eef2f7;
  --up:#2a78d6;--down:#e34948;--flat:#7d8898;--w1:#86b6ef;--w2:#2a78d6;--w3:#104281;
  --claude:#178a62;--claude-bg:#e2f6ee;--bad:#d03b3b;--warn:#7a5200;--warn-bg:#fdf1d3;--good:#0a6a0a;--good-bg:#e5f5e5;
  --syn-key:#1f4a85;--syn-str:#1d6b4f;--syn-num:#a8481a;--syn-lit:#7a3fa0;
  --shadow:0 1px 2px rgba(18,25,35,.06),0 6px 18px rgba(18,25,35,.04);
  --sans:'Schibsted Grotesk',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --mono:'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,Consolas,monospace;
  --gutter:clamp(16px,4vw,32px);
  color-scheme:light;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#0e131a;--surface:#151b24;--surface-2:#1a212c;--line:#283140;--line-strong:#38445a;
    --ink:#eceff4;--ink-2:#b2bccb;--ink-3:#8995a6;--accent:#8ab8f2;--accent-soft:#1a2a42;--code-bg:#1d2632;
    --up:#3987e5;--down:#e66767;--flat:#6b7789;--w1:#6da7ec;--w2:#2a78d6;--w3:#184f95;
    --claude:#3fbf94;--claude-bg:#10261f;--bad:#f09090;--warn:#f1c257;--warn-bg:#33290f;--good:#7fd67f;--good-bg:#12301a;
    --syn-key:#8ab8f2;--syn-str:#7fd6b0;--syn-num:#f0a070;--syn-lit:#c9a0f0;--shadow:none;color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --ground:#0e131a;--surface:#151b24;--surface-2:#1a212c;--line:#283140;--line-strong:#38445a;
  --ink:#eceff4;--ink-2:#b2bccb;--ink-3:#8995a6;--accent:#8ab8f2;--accent-soft:#1a2a42;--code-bg:#1d2632;
  --up:#3987e5;--down:#e66767;--flat:#6b7789;--w1:#6da7ec;--w2:#2a78d6;--w3:#184f95;
  --claude:#3fbf94;--claude-bg:#10261f;--bad:#f09090;--warn:#f1c257;--warn-bg:#33290f;--good:#7fd67f;--good-bg:#12301a;
  --syn-key:#8ab8f2;--syn-str:#7fd6b0;--syn-num:#f0a070;--syn-lit:#c9a0f0;--shadow:none;color-scheme:dark;
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:17px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
h1,h2,h3{margin:0;font-weight:600;line-height:1.2;text-wrap:balance}
p{margin:0}ul,ol{margin:0;padding:0;list-style:none}
code{font-family:var(--mono)}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.wrap{max-width:1040px;margin:0 auto;padding-inline:var(--gutter);padding-block:26px 72px}
.eyebrow{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;font:500 13.5px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
h1{margin-top:14px;font-size:clamp(30px,5.4vw,44px);font-weight:700;letter-spacing:-.015em}
.purpose{margin-top:12px;color:var(--ink-2);font-size:18px;max-width:64ch}
.jump{position:sticky;top:0;z-index:3;display:flex;flex-wrap:wrap;gap:8px;margin-top:18px;padding:10px 0;background:var(--ground)}
@media(max-width:640px){.jump{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;margin-inline:calc(-1 * var(--gutter));padding-inline:var(--gutter)}.jump::-webkit-scrollbar{display:none}.jump .chip{flex:none}}
.chip{display:inline-flex;align-items:center;min-height:28px;padding:3px 11px;border:1px solid var(--line-strong);border-radius:999px;font:500 13.5px/1.1 var(--mono);color:var(--ink-2);background:var(--surface);text-decoration:none}
a.chip:hover{background:var(--surface-2);color:var(--ink)}
.tag{display:inline-block;font:500 12px/1 var(--mono);text-transform:uppercase;letter-spacing:.05em;padding:4px 6px;border-radius:4px;background:var(--accent-soft);color:var(--accent)}
.st{display:inline-block;font:500 12px/1 var(--mono);text-transform:uppercase;letter-spacing:.05em;padding:4px 7px;border-radius:4px}
.st.shadow{background:var(--surface-2);color:var(--ink-3);border:1px solid var(--line)}
.st.dark{background:var(--surface-2);color:var(--ink-3);border:1px dashed var(--line-strong)}
.sec{margin-top:48px}
.sec>h2{font-size:28px;letter-spacing:-.005em}
.sub{margin-top:8px;color:var(--ink-2);max-width:72ch;font-size:17px}
.k{font:500 12px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);margin-right:8px}

/* diagrams */
.fig{margin:16px 0 0}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--surface);box-shadow:var(--shadow)}
.scroll svg{display:block;width:100%;min-width:960px;height:auto;color:var(--ink-2)}
@media(max-width:640px){.scroll::after{content:"scroll sideways to see the whole drawing";display:block;padding:6px 12px 8px;border-top:1px solid var(--line);font:500 12px/1.3 var(--mono);color:var(--ink-3)}}
.dg-box{fill:var(--surface);stroke:var(--ink-3);stroke-width:1.4}
.dg-box.plan{stroke-dasharray:6 4}
.dg-box.miss{stroke:var(--bad);stroke-dasharray:2 4;stroke-width:1.8}
.dg-box.jev{fill:var(--accent-soft);stroke:var(--accent);stroke-width:2}
.dg-box.claude{fill:var(--claude-bg);stroke:var(--claude);stroke-width:1.6}
.dg-box.soft{fill:var(--surface-2)}
.dg-key{fill:none;stroke:var(--line-strong);stroke-width:1}
.dg-t{fill:var(--ink);font:600 13px var(--sans)}
.dg-s{fill:var(--ink-2);font:400 12px var(--sans)}
.dg-l{fill:var(--ink-3);font:500 11.5px var(--sans)}
.dg-tier{fill:var(--ink-3);font:500 11px var(--mono);letter-spacing:.08em}
.dg-arrow{stroke:currentColor;stroke-width:1.4;fill:none}
.dg-n{fill:var(--accent);font:500 11.5px var(--mono);text-decoration:underline}
.dg-click{cursor:pointer;outline:none}
.dg-click .dg-box{stroke:var(--accent);stroke-width:2}
.dg-click:hover .dg-box,.dg-click:focus-visible .dg-box{fill:var(--accent-soft);stroke-width:2.6}
figcaption{margin-top:12px;color:var(--ink-2);font-size:16px;max-width:92ch}

/* the worked example, a modal */
dialog.modal{border:1px solid var(--line-strong);border-radius:14px;background:var(--surface);color:var(--ink);padding:0;width:min(760px,calc(100vw - 32px));max-height:calc(100vh - 32px);max-height:calc(100dvh - 32px);box-shadow:0 20px 60px rgba(0,0,0,.35)}
dialog.modal::backdrop{background:rgba(8,12,18,.55)}
.mod-h{display:flex;align-items:flex-start;gap:14px;padding:18px 20px 10px}
@media(max-width:640px){.mod-h{flex-wrap:wrap}}
.mod-h h3{font-size:19px}
.mod-h p{margin-top:6px;font-size:14.5px;color:var(--ink-2);line-height:1.45}
.mod-x{flex:none;padding:7px 12px;font-size:14px}
.mod-b{padding:4px 20px 20px;display:grid;gap:8px}
.ml{padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2)}
.ml code{display:block;font:500 14px/1.5 var(--mono);overflow-wrap:anywhere}
.ml p{margin-top:4px;font-size:14.5px;color:var(--ink-2);line-height:1.45}
.ml .hi{color:var(--accent)}

/* rows with a code head, a badge and a plain-words cell */
.opt{font-size:13px;line-height:1.3;padding:3px 8px;border-radius:6px;background:var(--surface-2);border:1px solid var(--line);color:var(--ink-2)}
.why{font-size:13.5px;color:var(--ink-2);line-height:1.4}
.rvh{margin-top:22px;font-size:19px}
.rvl{margin-top:8px;display:grid;gap:6px}
.rv{display:grid;gap:4px 12px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
.rv .qid{font:500 14px/1.3 var(--mono);overflow-wrap:anywhere}
.rv .why{font-size:15px}
@media(min-width:760px){.rv{grid-template-columns:minmax(0,1fr) auto minmax(0,1.9fr);align-items:center}}

/* buttons and the folded raw records */
.jtools{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;margin-top:12px}
.btn{border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);border-radius:8px;padding:9px 14px;font:500 15px/1 var(--sans);cursor:pointer}
.btn:hover{background:var(--surface-2)}
.stt{font-size:15px;color:var(--ink-3)}
.sg{margin-top:10px;border:1px solid var(--line);border-radius:10px;background:var(--surface);overflow:hidden}
.sg>summary{display:flex;align-items:center;gap:12px;padding:10px 14px;cursor:pointer;list-style:none}
.sg>summary::-webkit-details-marker{display:none}
.sg>summary::before{content:"\25B8";color:var(--ink-3);display:inline-block;transition:transform .12s}
.sg[open]>summary::before{transform:rotate(90deg)}
.sgid{font:500 15px/1.3 var(--sans)}
.sgc{font-size:13.5px;color:var(--ink-3)}
.sgcode{border-top:1px solid var(--line);background:var(--surface-2);padding:12px 14px 14px;font:400 13px/1.6 var(--mono);color:var(--ink);overflow-x:auto}
.sgcode+.sgcode{border-top:0}
.sgcode h4{margin:0 0 8px;font:600 14px/1.3 var(--sans);color:var(--ink-2)}
.ln{overflow-wrap:anywhere}
.jk{color:var(--syn-key)}.js{color:var(--syn-str)}.jn{color:var(--syn-num)}.jl{color:var(--syn-lit)}
.foot{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);font-size:14px;color:var(--ink-3)}

/* the weights */
.tally{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.tl{display:flex;flex-direction:column;gap:2px;padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--surface);min-width:120px}
.tl .k{margin:0}
.tl b{font:600 20px/1.1 var(--sans);color:var(--ink)}
.tscroll{overflow-x:auto;margin-top:8px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}
table.db{width:100%;border-collapse:collapse;font:400 15px/1.4 var(--sans)}
table.db th{position:sticky;top:0;background:var(--surface-2);color:var(--ink-3);font:500 13px/1.2 var(--mono);letter-spacing:.05em;text-transform:uppercase;text-align:left;padding:10px 12px;border-bottom:1px solid var(--line-strong)}
table.db td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top;color:var(--ink)}
table.db tbody tr:last-child td{border-bottom:0}
table.db tbody tr:nth-child(even) td{background:var(--surface-2)}
table.db .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
table.db td.ask{width:58%;min-width:200px}
table.db td.ask code{display:block;font:400 13px/1.4 var(--mono);color:var(--ink-3);margin-top:2px}
table.db td.w{width:26%;min-width:110px;vertical-align:middle}
.wbar{height:10px;border-radius:5px;background:var(--code-bg);overflow:hidden}
.wbar i{display:block;height:10px;background:var(--accent);border-radius:5px}
table.db tr.out td{color:var(--ink-3)}
table.db tr.out .wbar i{background:var(--bad)}
@media(max-width:640px){
  table.db thead,table.db td.w{display:none}
  table.db,table.db tbody,table.db tr{display:block}
  table.db tr{padding:10px 0 8px;border-bottom:1px solid var(--line)}
  table.db tbody tr:last-child{border-bottom:0}
  table.db td{display:block;border-bottom:0;padding:0 12px}
  table.db td.ask{width:auto;min-width:0;padding-bottom:6px}
  table.db td.num{display:inline-block;text-align:left;padding:0 14px 0 12px;font-size:14px;color:var(--ink-2)}
  table.db td.num::before{content:attr(data-k) " ";color:var(--ink-3);font:500 11px var(--mono);text-transform:uppercase;letter-spacing:.05em}
  table.db tbody tr:nth-child(even) td{background:transparent}
}

/* label to question map */
details.vp{margin-top:14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}
details.vp>summary{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline;padding:16px 20px;cursor:pointer;list-style:none}
details.vp>summary::-webkit-details-marker{display:none}
details.vp>summary::before{content:"\25B8";color:var(--ink-3);font-size:18px;margin-right:2px;display:inline-block;transition:transform .12s}
details.vp[open]>summary::before{transform:rotate(90deg)}
.vp-t{font-size:21px;font-weight:600}
.vp-c{font:400 14px/1.4 var(--mono);color:var(--ink-3)}
.vp-body{border-top:1px solid var(--line);padding:4px 20px 12px}
details.lab{border-top:1px solid var(--line)}
details.lab:first-child{border-top:0}
details.lab>summary{display:grid;gap:6px;padding:14px 0;cursor:pointer;list-style:none}
details.lab>summary::-webkit-details-marker{display:none}
.lab-head{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:baseline}
.lab-head::before{content:"\25B8";color:var(--ink-3);font-size:15px;display:inline-block;transition:transform .12s}
details.lab[open] .lab-head::before{transform:rotate(90deg)}
.lab-head .qid{font:500 15.5px/1.3 var(--mono);color:var(--ink);overflow-wrap:anywhere}
.lab-n{font:500 12.5px/1 var(--mono);letter-spacing:.04em;text-transform:uppercase;color:var(--accent);background:var(--accent-soft);padding:5px 8px;border-radius:5px}
.lab-s{font-size:17px;line-height:1.55;color:var(--ink-2);padding-left:22px}
.lab-s q{quotes:"\201C" "\201D"}
.lab-body{padding:0 0 14px 22px;display:grid;gap:10px}
.mq{padding:10px 0 10px 16px;border-left:3px solid var(--line-strong)}
.mq.shadow{border-left-style:dashed}
.mq-ask{font-size:17px;font-weight:600;line-height:1.4}
.mq-opts{margin-top:4px;font-size:15px;color:var(--ink-3);line-height:1.5}
.mq-none{font-size:16px;color:var(--ink-3);padding:4px 0 6px}
"""

# ------------------------------------------------------------------ svg helpers

def box(x, y, w, h, title, subs, cls="", rx=7):
    s = f'<rect class="dg-box {cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/>'
    s += f'<text class="dg-t" x="{x + 12}" y="{y + 22}">{E(title)}</text>'
    for i, line in enumerate(subs):
        s += f'<text class="dg-s" x="{x + 12}" y="{y + 40 + i * 16}">{E(line)}</text>'
    return s


def arrow(points, label=None, lx=0, ly=0, dashed=False, anchor="start"):
    pts = " ".join(f"{a},{b}" for a, b in points)
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    s = f'<polyline class="dg-arrow" points="{pts}"{dash} marker-end="url(#ah)"/>'
    if label:
        s += f'<text class="dg-l" x="{lx}" y="{ly}" text-anchor="{anchor}">{E(label)}</text>'
    return s


DEFS = '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0 0 L10 5 L0 10 z" fill="currentColor"/></marker></defs>'


# ------------------------------------------------------------------ diagram 1: how it fits together

def fit_diagram():
    p = [DEFS]
    for label, y in [("FEEDS", 62), ("MIRAI JOBS", 154), ("MIRAI FILES", 244), ("SERVED", 556), ("PHONE", 650)]:
        p.append(f'<text class="dg-tier" x="12" y="{y}">{label}</text>')
    # feeds
    p.append(box(128, 24, 250, 68, "Schwab", ["live SNDK price and 1-minute bars"]))
    p.append(box(394, 24, 250, 68, "ThetaData", ["the weekly options book"]))
    p.append(box(660, 24, 282, 68, "News feed", ["none live; the 8 news questions wait"], "miss"))
    # mirai jobs
    p.append(box(128, 118, 390, 62, "SNDK PRO scan, every 120 s", ["one diary row: price, options weight by strike, walls, IV"]))
    p.append(box(534, 118, 180, 62, "Bar sidecar", ["keeps the minute bars"]))
    p.append(box(730, 118, 212, 62, "The Reader (Claude)", ["one call, its notes, unchanged"], "claude"))
    # mirai files
    p.append(box(128, 208, 814, 60, "Mirai's state files, never written by JEV",
                 ["diary rows every 120 s, minute bars, side packets, the chain cache, the Reader's notes"]))
    # the service lane
    p.append('<rect class="dg-key" x="118" y="292" width="834" height="196" rx="9" style="stroke-dasharray:7 5;stroke:var(--accent)"/>')
    p.append('<text class="dg-tier" x="300" y="312" style="fill:var(--accent)">JEV DECISION SERVICE: ITS OWN JOB AT :02 AND :32, WRITES ONLY state/jev/</text>')
    y = 332
    p.append(box(128, y, 150, 84, "Scene loader", ["newest row, done bars,", "prior days, side packet,", "the chain; no peeking"]))
    p.append(box(294, y, 170, 84, "State builder", [f"{STATUS_COUNT['built']} labels in sigma units,", "the cut written into", "every sentence"]))
    p.append(box(480, y, 130, 84, "Packer", [f"{N_G_SENT} requests, one", "per question group;", "no label, not asked"]))
    p.append(box(626, y, 170, 84, "JEV, the fast judge", [f"{n_live} live + {n_shadow} shadow", "questions in parallel, then", "two sums in one request"], "jev"))
    p.append(box(812, y, 130, 84, "Writer, grader", ["{day}.jsonl, hour/,", "grades, weights,", "latest.json"]))
    for x0 in (278, 464, 610, 796):
        p.append(arrow([(x0, y + 42), (x0 + 16, y + 42)]))
    # served
    p.append(box(128, 520, 300, 68, "Viewstation :8787, unchanged", ["already serves state files read-only", "over /api/raw/file; no new route"]))
    p.append(box(460, 520, 290, 68, "state/jev/latest.json", ["the newest run: situation, answers,", "the two sums, what was held or missing"]))
    p.append(box(780, 520, 162, 68, "ntfy push", ["later: when a", "sum flips"], "plan"))
    # phone
    p.append(box(128, 614, 300, 66, "/m/jev.html, the decision card", ["polls every 60 s; the two sums, each", "answer as odds, held and dark marked"]))
    p.append(box(460, 614, 290, 66, "/m glance", ["the Reader's notes, unchanged"]))
    p.append(box(780, 614, 162, 66, "Grading, live", ["every run the bars", "grade the two sums"]))
    # arrows
    p.append(arrow([(253, 92), (253, 118)], "price, bars", 261, 110))
    p.append(arrow([(519, 92), (519, 118)], "chain", 527, 110))
    p.append(arrow([(323, 180), (323, 208)], "row every 120 s", 331, 200))
    p.append(arrow([(624, 180), (624, 208)], "minute bars", 632, 200))
    p.append(arrow([(836, 180), (836, 208)], "its notes", 844, 200))
    p.append(arrow([(203, 268), (203, 332)], "reads, never writes", 211, 286))
    p.append(arrow([(877, 416), (877, 502), (605, 502), (605, 520)], "latest.json, every run", 700, 496, anchor="middle"))
    p.append(arrow([(460, 551), (428, 551)], "reads", 444, 545, anchor="middle"))
    p.append(arrow([(278, 588), (278, 614)], "GET every 60 s", 286, 606))
    p.append(arrow([(400, 588), (400, 600), (605, 600), (605, 614)], "the notes", 613, 611))
    return ('<svg viewBox="0 0 960 692" role="img" aria-label="How the JEV decision feature sits beside Mirai station: Schwab and ThetaData feed the SNDK PRO scan and the bar sidecar, which write Mirai\'s state files; the JEV decision service is its own job that reads those files, builds the labels, asks JEV and writes state/jev/latest.json; the unchanged viewstation serves that file read-only and the phone\'s decision card polls it; the bars grade the sums every run; push comes later.">'
            + "".join(p) + "</svg>")


# ------------------------------------------------------------------ diagram 2: the state builder, in detail

def state_diagram():
    p = [DEFS]
    for label, y in [("INPUTS", 62), ("SCENE", 175), ("LABELLER", 282), ("", 0), ("STATE", 478), ("PACKER", 598), ("JEV", 690)]:
        if label:
            p.append(f'<text class="dg-tier" x="12" y="{y}">{label}</text>')
    # inputs
    p.append(box(128, 24, 190, 68, "Diary row", ["price, sigma, vwap, IV,", "options weight and walls"]))
    p.append(box(330, 24, 190, 68, "Minute bars", ["open, high, low, close, volume"]))
    p.append(box(532, 24, 190, 68, "Prior sessions", ["the same files, earlier days"]))
    p.append(box(734, 24, 208, 68, "Side packets", ["level visits, RSI episodes"]))
    for cx in (223, 425, 627, 838):
        p.append(arrow([(cx, 92), (cx, 138)]))
    # scene
    p.append(box(128, 140, 814, 66, "Pick the moment",
                 ["the row's own timestamp is now; only bars that had finished before it count",
                  "prior sessions come only from earlier days, so a replay never sees the future"]))
    p.append(arrow([(535, 206), (535, 240)], "one moment, no look-ahead", 545, 226))
    # labeller: four transforms
    p.append(box(128, 244, 190, 78, "1  Scale by sigma", ["dollars become today's units", "$42 above vwap is 0.88 sigma"], "soft"))
    p.append(box(330, 244, 190, 78, "2  Rank against history", ["same clock slot, prior sessions", "higher than 13 of 16 sessions"], "soft"))
    p.append(box(532, 244, 190, 78, "3  Share inside the snapshot", ["one part over the whole", "60% of the weight sits above"], "soft"))
    p.append(box(734, 244, 208, 78, "4  Compare with earlier", ["same or different, or how much", "IV changed +1.8 vol points"], "soft"))
    for cx in (223, 425, 627, 838):
        p.append(arrow([(cx, 322), (cx, 346)]))
    p.append(box(128, 348, 630, 62, "Judge against a fixed cut, then write one sentence carrying the number and the cut",
                 ["0.15 sigma move rule, 0.5 sigma wall, top and bottom fifth, 2 vol points; the cut is in the words"]))
    p.append(arrow([(758, 379), (786, 379)]))
    p.append(box(788, 348, 154, 68, "Cannot measure it?", ["leave the label out", "and record why"]))
    p.append(arrow([(443, 410), (443, 444)], "labels, grouped by viewpoint", 453, 430))
    # state tiles
    tiles = []
    for vp in SPEC["viewpoints"]:
        labs = [l for l in SPEC["labels"] if l["viewpoint"] == vp["id"]]
        tiles.append((VP_SHORT.get(vp["id"], vp["id"]), len(labs)))
    for i, (name, n) in enumerate(tiles):
        x = 128 + i * 118
        p.append(box(x, 446, 106, 90, name, [f"{n} labels", "all built"]))
    p.append(arrow([(535, 536), (535, 570)], f"up to {STATUS_COUNT['built']} sentences; one that cannot be measured is left out", 545, 556))
    p.append(box(128, 572, 814, 58, "Packer", ["one request per question group, only that group's sentences, context always included; a question missing a label is skipped"]))
    p.append(arrow([(535, 630), (535, 660)], f"{N_G_SENT} requests, in parallel", 545, 648))
    p.append(box(128, 662, 814, 58, "JEV", [f"{N_Q_SENT} questions answered at once; a probability per answer option; under a second for the lot"], "jev"))
    return ('<svg viewBox="0 0 960 744" role="img" aria-label="The state builder in detail: four inputs, the moment is pinned with no look-ahead, four transforms turn numbers into sentences with the cut written in, unmeasurable labels are left out, the labels group into seven viewpoints, the packer cuts one request per group, and JEV answers in parallel.">'
            + "".join(p) + "</svg>")


# ------------------------------------------------------------------ json, highlighted

def highlight(text):
    t = html.escape(text, quote=False)
    pat = re.compile(r'("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)')
    def rep(m):
        s = m.group(0)
        c = "jn"
        if s.startswith('"'):
            c = "jk" if s.endswith(":") else "js"
        elif s in ("true", "false", "null"):
            c = "jl"
        return f'<span class="{c}">{s}</span>'
    return pat.sub(rep, t)


def ln_html(obj):
    rows = []
    for line in json.dumps(obj, indent=2, ensure_ascii=False).split("\n"):
        s = line.lstrip(" ")
        ind = len(line) - len(s)
        rows.append(f'<div class="ln" style="padding-left:{ind + 4}ch;text-indent:-4ch">{highlight(s)}</div>')
    return "".join(rows)


# ------------------------------------------------------------------ label to question mapping

Q_BY_ID = {qid: (gid, q) for gid, qid, q in QS}


def qmini(qid):
    gid, q = Q_BY_ID[qid]
    st = q_status(q)
    ask = q.get("ask") or q["instructions"]
    crit = q["criteria"]
    opts = ["Yes", "No"] if q["type"] == "noul" else [c.replace("_", " ").capitalize() for c in crit]
    shadow = ' <span class="st shadow">shadow</span>' if st == "shadow" else (' <span class="st dark">dark, no news source</span>' if st == "dark" else "")
    return (f'<div class="mq{" shadow" if st == "shadow" else ""}"><div class="mq-ask">{E(ask)}{shadow}</div>'
            f'<div class="mq-opts">{E(" · ".join(opts))}</div></div>')


def mapping_section():
    n_read = sum(1 for l in SPEC["labels"] if not l["path"].startswith("context.") and read_by(l["path"]))
    n_multi = sum(1 for l in SPEC["labels"] if not l["path"].startswith("context.") and len(read_by(l["path"])) > 1)
    n_ctx = sum(1 for l in SPEC["labels"] if l["path"].startswith("context."))
    n_none = len(SPEC["labels"]) - n_ctx - n_read
    out = [f'<p class="sub">{len(SPEC["labels"])} labels. {n_ctx} are framing, sent with every request. {n_read} are read by at least one question, {n_multi} of those by more than one. {n_none} are read by no question yet and sit in the state as labels only.</p>'
           '<div class="jtools"><button class="btn" type="button" id="mex">Expand all</button><button class="btn" type="button" id="mco">Collapse all</button><span class="stt">Open a viewpoint, then a label, to see the questions that read it.</span></div>']
    for i, vp in enumerate(SPEC["viewpoints"]):
        labs = [l for l in SPEC["labels"] if l["viewpoint"] == vp["id"]]
        qids = list(dict.fromkeys(q for l in labs for q in read_by(l["path"]) if not l["path"].startswith("context.")))
        rows = []
        for l in labs:
            ex = ' <span class="tag">example wording</span>' if not l.get("real") else ""
            if l["path"].startswith("context."):
                rb, count = [], "sent with every request"
                body = '<div class="mq-none">Framing for JEV. Every request carries it; no question names it.</div>'
            else:
                rb = read_by(l["path"])
                count = plural_q(len(rb))
                body = "".join(qmini(q) for q in rb) if rb else '<div class="mq-none">No question reads it yet.</div>'
            rows.append(f'<details class="lab"><summary><span class="lab-head"><code class="qid">{E(l["path"])}</code><span class="lab-n">{E(count)}</span></span>'
                        f'<span class="lab-s"><q>{E(l["reads_as"])}</q>{ex}</span></summary><div class="lab-body">{body}</div></details>')
        out.append(f'<details class="vp"{" open" if i == 0 else ""}><summary><span class="vp-t">{E(vp["title"])}</span><span class="vp-c">{len(labs)} labels · {len(qids)} questions read them</span></summary>'
                   f'<div class="vp-body">{"".join(rows)}</div></details>')
    js = """
(function(){
  function all(o){ document.querySelectorAll('details.vp, details.lab').forEach(function(d){ d.open = o; }); }
  document.getElementById('mex').addEventListener('click', function(){ all(true); });
  document.getElementById('mco').addEventListener('click', function(){ all(false); });
})();
"""
    return "".join(out) + f"<script>{js}</script>"


def plural_q(n):
    return "no question yet" if n == 0 else "1 question" if n == 1 else f"{n} questions"


# ------------------------------------------------------------------ the pipeline (steps 1 to 6) and its files

HOUR_DOC = load_json(SKILL / "questions" / "sndk_hour.json", "the sums' doc")
(H1_MIN, H1_BAND), (H2_MIN, H2_BAND) = ((int(HOUR_DOC["horizons"][q]["minutes"]), float(HOUR_DOC["horizons"][q]["flat_band_sigma"])) for q in HOUR_QIDS)
HOUR_LINE = None
if (STATE / "jev" / "hour").exists():
    files = sorted((STATE / "jev" / "hour").glob("*.jsonl"))
    if files:
        lines = _jsonl(files[-1])
        HOUR_LINE = lines[-1] if lines else None


def pipeline_diagram():
    p = [DEFS]
    steps = [
        ("1  Labels", "code", [f"{STATUS_COUNT['built']} sentences from the", "row, bars, side packet"], ""),
        ("2  The questions", "JEV, in parallel", [f"{n_live} live + {n_shadow} shadow", "asked when due, else", "the last answer held"], "jev"),
        ("3  Answers as words", "code", ["'...? rising, 100% sure'", "under the cut left out"], ""),
        ("4  Two sums", "JEV", [f"price in {H1_MIN} and {H2_MIN} min:", "Up, Down, Flat, Unsure"], "jev"),
        ("5  The card", "file, phone", ["state/jev/latest.json", "/m/jev.html polls it"], ""),
    ]
    x = 128
    for i, (title, who, subs, cls) in enumerate(steps):
        p.append(box(x, 40, 150, 102, title, [who] + subs, cls))
        if i < len(steps) - 1:
            p.append(arrow([(x + 150, 91), (x + 166, 91)]))
        x += 166
    p.append(box(294, 200, 340, 84, "6  Grading", ["code, every run; each mark graded as it passes", f"price {H1_MIN} and {H2_MIN} min later vs the row's price,", "in sigma units; a hit or a miss, and how far off"]))
    p.append('<g class="dg-click" id="wbox" role="button" tabindex="0" aria-haspopup="dialog" aria-controls="dlg-tracking">'
             + box(650, 200, 292, 100, "Weights", ["per question: how well its fresh", "picks tracked the outcome; 1.0 until", f"{NEED} graded reads of its own, cut at {CUT}"])
             + '<text class="dg-n" x="662" y="292">▸ click for the math, worked through</text></g>')
    p.append(arrow([(866, 142), (866, 172), (452, 172), (452, 200)], "the picks, the row's price, sigma, what was used", 560, 192))
    p.append(arrow([(634, 242), (650, 242)]))
    p.append(arrow([(796, 300), (796, 316), (535, 316), (535, 142)], "which answers speak on the next run", 545, 332, dashed=True))
    p.append('<text class="dg-tier" x="12" y="95">RUN</text><text class="dg-tier" x="12" y="246">LOOP</text>')
    return (f'<svg viewBox="0 0 960 344" role="img" aria-label="The six-step pipeline: labels, the live questions, answers as sentences, two sums, {H1_MIN} and {H2_MIN} minutes ahead, the card; grading feeds weights back into which answers are used. The Weights box opens a worked example of the tracking score.">'
            + "".join(p) + "</svg>")


def tracking_modal():
    """The tracking score worked through on one made-up pairing, opened from the Weights box.

    The arithmetic is fixed at 40 reads (12 of 40 = 0.30 and so on), a worked example, not the cut."""
    lines = [
        ("how often Active was followed by Up = 12 of 40 reads = 0.30",
         "On 12 of the 40 graded reads the question answered Active and price then went Up, so this pairing happened 30% of the time."),
        ("how often the question answered Active = 20 of 40 reads = 0.50",
         "The question answered Active on 20 of the 40 reads, whatever price did afterwards, so Active came up half the time."),
        ("how often price went Up = 14 of 40 reads = 0.35",
         f"Price was Up {H1_MIN} minutes later on 14 of the 40 reads, whatever the question had said, so Up happened 35% of the time."),
        ("how often Active and Up would meet by chance = 0.50 x 0.35 = 0.175",
         "If the answer had nothing to do with the outcome, Active and Up would still land on the same read about 17.5% of the time, purely by chance."),
        ("this pairing's credit = 0.30 x ln(0.30 / 0.175) = 0.30 x 0.539 = <span class=\"hi\">+0.162</span>",
         "Active then Up really happened 30% of the time, more than the 17.5% chance alone would give, so this answer does point to Up; the log measures how far above chance that is, and the 0.30 in front counts it by how often the pairing happens."),
        ("tracking score = the six pairings' credits added up = <span class=\"hi\">0.204</span>",
         "The same five lines are run for the other five pairings (Active then Flat, Active then Down, Quiet then Up, Quiet then Flat, Quiet then Down), and the six credits added together are the question's tracking score."),
    ]
    body = "".join(f'<div class="ml"><code>{f}</code><p>{E(s)}</p></div>' for f, s in lines)
    js = r"""
(function(){
  var d = document.getElementById('dlg-tracking'), b = document.getElementById('wbox');
  if (!d) return;
  function open(e){ if (e) e.preventDefault(); if (d.showModal) d.showModal(); else d.setAttribute('open', ''); }
  if (b) { b.addEventListener('click', open); b.addEventListener('keydown', function(e){ if (e.key === 'Enter' || e.key === ' ') open(e); }); }
  // the link in the Weights section is further down the page than this script, so listen on the document
  document.addEventListener('click', function(e){ if (e.target.closest && e.target.closest('#wmath')) open(e); });
  d.querySelector('.mod-x').addEventListener('click', function(){ d.close(); });
  d.addEventListener('click', function(e){ if (e.target === d) d.close(); });
  document.addEventListener('keydown', function(e){ if (e.key === 'Escape' && d.open) d.close(); });
})();
"""
    return (f'<dialog class="modal" id="dlg-tracking" aria-labelledby="dlg-tracking-h">'
            f'<div class="mod-h"><div><h3 id="dlg-tracking-h">One pairing, Active then Up, line by line</h3>'
            f'<p>A made-up example over 40 graded reads of a made-up two-answer question (Active or Quiet): "Is the tape, the flow of trades, active right now, or quiet?". On each read its fresh answer is set beside what price did {H1_MIN} minutes later (Up, Flat or Down). Here: 12 reads answered Active and then went Up, Active was answered 20 times in all, and price went Up 14 times in all.</p></div>'
            f'<button class="btn mod-x" type="button" aria-label="Close">Close</button></div>'
            f'<div class="mod-b">{body}</div></dialog><script>{js}</script>')


def schema_section():
    hq = HOUR_DOC["questions"]
    hour_json = {"id": "hour", "state": {"context": {"symbol": "SNDK", "horizon": f"the next {H1_MIN} minutes, and the next {H2_MIN} minutes", "units": "..."},
                                        "answers": {"price_recent_direction": "Over the last 30 minutes, did price rise, fall, or go nowhere? rising, JEV was 100% sure",
                                                    "volume_now": "Is trading heavy, normal, or light for this time of day? normal, JEV was 98% sure",
                                                    "...": f"one sentence per answered live question, {n_live} today"}},
                 "questions": {qid: {k: q[k] for k in ("type", "instructions", "criteria")} for qid, q in hq.items()}}
    real = ""
    if HOUR_LINE:
        few = lambda d: {k: d[k] for k in list(d)[:3]} | {"...": f"{len(d)} in all"} if d else d
        real = {"row_ts": HOUR_LINE["row_ts"], "spot": "...", "sigma": HOUR_LINE["sigma"], "primary": HOUR_LINE.get("primary"),
                "by": HOUR_LINE.get("by"), "used": few(HOUR_LINE.get("used") or {}), "fresh": few(HOUR_LINE.get("fresh")),
                "left_out": HOUR_LINE.get("left_out"), "missing": HOUR_LINE.get("missing"), "sentences": "...", "request": "..."}
        real = {k: v for k, v in real.items() if v is not None}
    files = [
        ("state/jev/{day}.jsonl", "every run", "row_ts, sigma, state (the labels), omitted, requests, skipped, held {qid: since}, cadence_from, answers (JEV's raw replies), hour"),
        ("state/jev/latest.json", "the phone's card", f"symbol, row_ts, freshness, situation (5 plain lines), questions[] with answer, held_from or skipped, hour (its name; the {H1_MIN}-minute sum on top, next_60 under by)"),
        ("state/jev/hour/{day}.jsonl", "what step 6 grades", "row_ts, spot, sigma, by {next_30, next_60: pick, probabilities}, used {qid: pick}, fresh {qid: pick, the ones asked afresh}, left_out {qid: why}, missing [qid: no label and nothing held], sentences, request"),
        ("state/jev/grades.jsonl", "one line per graded mark", "row_ts, horizons (which sums this line grades), pending, skipped, then per sum: realized_sigma, band, pick, hit, brier; fresh; or graded false with the reason, for a read that can never be graded"),
        ("state/jev/cadence.json", "what the cadence plan reads", f"recounted_from (the day), questions {{qid: {{minutes {'/'.join(map(str, STEPS))}, p25_hold_min, changes, reads, why}}}}"),
        ("state/jev/last_asked.json", "the held answers", "qid: {row_ts of the last fresh answer, answer, moved}; a not-due question is served from here, tagged held from HH:MM"),
        ("state/jev/weights.json", "what step 3 reads", "graded_runs, sums {next_30, next_60: n, hit_rate, mean_brier}, questions {qid: {weight, mi (the tracking score, see below), n, in_step_3, why}}"),
        ("state/jev/weights_log.jsonl", "the weight history", "one line per grading run that graded something: the tally and every weight that moved"),
    ]
    rows = "".join(f'<div class="rv"><code class="qid">{E(a)}</code><span class="opt">{E(b)}</span><span class="why">{E(c)}</span></div>' for a, b, c in files)
    grade = f"""<div class="rvl">
<div class="rv"><code class="qid">realized</code><span class="opt">number</span><span class="why">(price {H1_MIN} minutes after the read minus price at the read) divided by sigma, the day's expected move; the {H2_MIN}-minute sum is graded the same way at {H2_MIN}</span></div>
<div class="rv"><code class="qid">band</code><span class="opt">up / flat / down</span><span class="why">up above +{H1_BAND}, down below -{H1_BAND}, flat between at {H1_MIN} minutes ({H2_BAND} at {H2_MIN}); the same bands the questions' criteria carry, measured on this name</span></div>
<div class="rv"><code class="qid">hit</code><span class="opt">yes / no</span><span class="why">JEV's pick equals the band; compared with always saying flat</span></div>
<div class="rv"><code class="qid">brier</code><span class="opt">0 best, 2 worst</span><span class="why">how far the odds sat from what happened: over up, flat and down, add up (odds given minus 1 or 0 for what happened) squared</span></div>
<div class="rv"><code class="qid">weight per question</code><span class="opt">0 to 1</span><span class="why">a tracking score: how much knowing the question's fresh pick tells you about the band (mutual information, in the code), divided by the best score among questions with {NEED} fresh pairs of their own; a question stays 1.0 until it has {NEED} pairs; under {CUT} its sentence is left out of step 3 but it is still asked, so it can climb back; a held answer never pairs</span></div>
<div class="rv"><code class="qid">when</code><span class="opt">at each mark</span><span class="why">the {H1_MIN}-minute sum is graded on the first run after its {H1_MIN} minutes have a bar (one at most {BAR_GAP_MAX_MIN} minutes before the mark), the {H2_MIN}-minute sum on the first run after its {H2_MIN}; the primary never waits for the slower one, and a run after both marks writes one line for both</span></div>
<div class="rv"><code class="qid">not graded</code><span class="opt">skipped</span><span class="why">a horizon ending more than {CLOSE_GRACE_MIN} minutes past the close (one inside those {CLOSE_GRACE_MIN} minutes is graded at the closing bar), or a day with no bars; a read none of whose horizons can be graded is closed out, never retried; a mark is graded once however many times the service ran on it</span></div>
<div class="rv"><code class="qid">missing</code><span class="opt">count on the card</span><span class="why">live questions that had no label this read and nothing held to fall back on; they are listed in the sum record and counted on the card, so a thin read is visible</span></div>
<div class="rv"><code class="qid">cadence</code><span class="opt">{' / '.join(map(str, STEPS))} min</span><span class="why">recounted once a day from the previous day's runs: take the hold time that a quarter of the question's holds fell under, halve it and snap to {STEPS[0]}, {STEPS[1]} or {STEPS[2]}; never changed all day gives {STEPS[1]} (three hours of runs) or {STEPS[2]} (five hours, ten reads); under {MIN_READS} reads keeps the last value. A change is the whole answer moving, not the picked word flipping: the odds of two consecutive answers are compared option by option and a total shift of {CHANGE_CUT} or more counts (heavy 0.90 to heavy 0.55 is a change, 0.90 to 0.88 is not)</span></div>
<div class="rv"><code class="qid">held</code><span class="opt">reused answer</span><span class="why">a question not yet due, or whose label is missing this read, keeps its last fresh answer for up to twice its cadence (never under an hour), tagged with the time it was given, on the card and in the sums' sentences. A question whose last two fresh answers moved {CHANGE_CUT} or more is in motion and is asked again on the next read whatever its cadence</span></div>
</div>"""
    raw = (f'<details class="sg"><summary><span class="sgid">The raw records, folded</span><span class="sgc">the step 4 request as sent, and the newest sum record</span></summary>'
           f'<div class="sgcode"><h4>The one request of step 4: two sums on the same sentences</h4>{ln_html(hour_json)}</div>'
           + (f'<div class="sgcode"><h4>The newest sum record, from the last run</h4>{ln_html(real)}</div>' if real else "") + '</details>')
    return (f'<h3 class="rvh">Files, all under state/jev/, written only by the service</h3><div class="rvl">{rows}</div>'
            f'<h3 class="rvh">How a read is graded, step 6</h3>{grade}{raw}')


# ------------------------------------------------------------------ the weight log, drawn from state/jev at build time

WLOG = _jsonl(STATE / "jev" / "weights_log.jsonl")
WEIGHTS_PATH = STATE / "jev" / "weights.json"
WEIGHTS = load_json(WEIGHTS_PATH, "weights.json", required=False)
CADENCE = load_json(STATE / "jev" / "cadence.json", "cadence.json", required=False)
NEED = WEIGHTS.get("min_graded", MIN_GRADED)
CUT = WEIGHTS.get("min_weight", MIN_WEIGHT)


def cadence_min(qid, q):
    m = ((CADENCE.get("questions") or {}).get(qid) or {}).get("minutes")
    return int(m) if isinstance(m, (int, float)) else parse_cadence(q.get("cadence"))


def stamp(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")


BUILT_AT = stamp(datetime.now())
WEIGHTS_AT = stamp(datetime.fromtimestamp(WEIGHTS_PATH.stat().st_mtime)) if WEIGHTS_PATH.is_file() else None


def weight_log_section():
    """Just the weight per question, grouped by viewpoint. 1.0 until graded; under the cut leaves the sum."""
    wq = WEIGHTS.get("questions", {})
    graded = WEIGHTS.get("graded_runs", 0)
    by_vp: dict[str, list] = {}
    for gid, qid, q in QS:
        if q_status(q) != "live":
            continue
        by_vp.setdefault(q_viewpoint(gid, q), []).append((qid, q))
    moved: dict[str, float] = {}
    moves: dict[str, int] = {}
    for line in WLOG:
        for c in line.get("changes", []):
            if c.get("before") is None:
                continue
            moved[c["question"]] = moved.get(c["question"], 0.0) + abs(float(c["after"]) - float(c["before"]))
            moves[c["question"]] = moves.get(c["question"], 0) + 1
    top = sorted(moved.items(), key=lambda kv: -kv[1])[:5]
    ask_of = {qid: (q.get("ask") or q["instructions"]) for _, qid, q in QS}
    strip = ("".join(f'<div class="tl"><span class="k">{E(qid)}</span><b>{v:.2f}</b><span class="k" style="text-transform:none;letter-spacing:0">{E(ask_of.get(qid, ""))}</span></div>' for qid, v in top)
             if top else f'<div class="tl"><span class="k">most adjusted</span><b>none yet</b><span class="k" style="text-transform:none;letter-spacing:0">no weight has moved: {graded} graded reads so far, {NEED} needed per question</span></div>')
    cad_note = (" (from " + E(CADENCE["recounted_from"]) + ")") if CADENCE.get("recounted_from") else ", not yet recounted so every question is on its starting value"
    w_note = (", last written " + E(WEIGHTS_AT)) if WEIGHTS_AT else ", not written yet"
    out = [f'<h3 class="rvh" style="margin-top:8px">Most adjusted questions, total movement so far</h3><div class="tally">{strip}</div>',
           f'<p class="sub">Every live question starts at 1.0. Once a question has {NEED} fresh graded reads its weight becomes how well its own answers tracked the next {H1_MIN} minutes, 1.0 for the best; under {CUT} the question stops feeding the sums but is still asked, so it can climb back. Graded reads so far: <b>{graded}</b>. The tracking score is how much the question\'s fresh picks so far tell you about what price did next (the "mi" field), the number the weight is built from once the {NEED} are in (<a href="#pipeline" id="wmath">see the math worked through</a>); 0.000 usually means the question gave the same answer on every graded read, or its answers so far say nothing about the band. Cadence is how often the question is asked afresh, recounted each day from the day before{cad_note}.</p>']
    for vp in [v for v in VP_ORDER if v in by_vp]:
        rows = []
        for qid, q in by_vp[vp]:
            w = wq.get(qid, {})
            weight = float(w.get("weight", 1.0))
            n = w.get("n", 0)
            cls = "out" if weight < CUT else ""
            score = float(w.get("mi", 0.0) or 0.0)
            rows.append(f'<tr class="{cls}"><td class="ask">{E(q.get("ask") or q["instructions"])}<code>{E(qid)}</code></td>'
                        f'<td class="w"><div class="wbar"><i style="width:{weight * 100:.0f}%"></i></div></td>'
                        f'<td class="num" data-k="weight">{weight:.2f}</td><td class="num" data-k="tracking">{score:.3f}</td>'
                        f'<td class="num" data-k="moved">{moved.get(qid, 0.0):.2f} <small>({moves.get(qid, 0)})</small></td><td class="num" data-k="graded">{n}</td>'
                        f'<td class="num" data-k="cadence">{"every read" if cadence_min(qid, q) == STEPS[0] else str(cadence_min(qid, q)) + " min"}</td></tr>')
        out.append(f'<h3 class="rvh">{E(VP_TITLE.get(vp, vp))}</h3>'
                   f'<div class="tscroll"><table class="db"><thead><tr><th>question</th><th class="w">weight</th><th class="num">value</th><th class="num">tracking score</th><th class="num">moved (times)</th><th class="num">graded reads</th><th class="num">cadence</th></tr></thead>'
                   f'<tbody>{"".join(rows)}</tbody></table></div>')
    out.append(f'<p class="sub" style="margin-top:12px;font-size:14px">A snapshot of state/jev/weights.json on the station{w_note}, taken when this page was built, {E(BUILT_AT)}. Every change is also kept in weights_log.jsonl.</p>')
    return "".join(out)


# ------------------------------------------------------------------ page

page = f"""<title>JEV decision service</title>
<style>{CSS}</style>
<div class="wrap" id="top">
  <header>
    <div class="eyebrow"><span>SNDK PRO</span><span>questions {E(QDOC.get("version", ""))}</span><span>labels {E(SPEC.get("version", ""))}</span><span>{E(QDOC.get("date", ""))}</span><span class="tag">Forecasts, never a call</span><span class="tag">Paper only</span></div>
    <h1>JEV decision service</h1>
    <p class="purpose">A separate job beside Mirai station: it reads what SNDK PRO already stores, turns the numbers into up to {STATUS_COUNT['built']} sentences (the news ones wait for a feed), has JEV answer {N_Q_SENT} small questions in parallel ({n_dark} more are dark until a news source is plugged in), and writes one card the phone polls. Below: how it fits, how a label is built, the six-step pipeline with its files, and which question reads which label.</p>
  </header>
  <nav class="jump" aria-label="Sections">
    <a class="chip" href="#fit">How it fits</a><a class="chip" href="#builder">The state builder</a><a class="chip" href="#pipeline">The pipeline</a><a class="chip" href="#wlog">Weights</a><a class="chip" href="#map">Label to question map</a><a class="chip" href="#top">Top</a>
  </nav>

  <section class="sec" id="fit">
    <h2>How it fits together</h2>
    <figure class="fig"><div class="scroll">{fit_diagram()}</div>
    <figcaption>Mirai station is unchanged: the scan, the bar sidecar and the Reader keep writing their files. The JEV decision service is its own launchd job, run at two minutes past each hour and half hour; it only reads those files and writes into state/jev/. The viewstation already serves any state file read-only, so the phone's decision card needs no new route. The dashed box comes later; the red dotted box is an input Mirai does not have. IV is implied volatility, the swing the options are pricing in; sigma is the day's expected move, the unit the labels are written in; a wall is a strike where dealers' hedging piles up.</figcaption></figure>
  </section>

  <section class="sec" id="builder">
    <h2>The state builder</h2>
    <figure class="fig"><div class="scroll">{state_diagram()}</div>
    <figcaption>Every label follows the same path: pin the moment, take the raw numbers, put them on a scale, judge against a cut, write one sentence with both the number and the cut in it. Sigma is the day's expected move, so the distance labels are in units of that move; vwap is the day's volume-weighted average price; vol points are points of implied volatility. A label that cannot be measured is left out rather than guessed, and the packer then skips any question that needed it.</figcaption></figure>
  </section>

  <section class="sec" id="pipeline">
    <h2>The pipeline, its own six steps</h2>
    <figure class="fig"><div class="scroll">{pipeline_diagram()}</div>
    <figcaption>One run per half hour, at :02 and :32. Steps 1 and 3 are code, steps 2 and 4 are JEV, step 5 is a file the phone polls. Step 6 runs every run and grades each sum the moment its own mark has a bar: the {H1_MIN}-minute sum at {H1_MIN} minutes, the {H2_MIN}-minute sum at {H2_MIN}, neither waiting for the other. A question's weight moves only once it has {NEED} fresh graded reads of its own. Click the Weights box to see the tracking score worked through, line by line, on a made-up pairing. The sums are forecasts: graded, never a call.</figcaption></figure>
    {tracking_modal()}
    {schema_section()}
  </section>

  <section class="sec" id="wlog">
    <h2>Weights</h2>
    {weight_log_section()}
  </section>

  <section class="sec" id="map">
    <h2>Label to question map</h2>
    {mapping_section()}
  </section>

  <p class="foot">Built from spec/labels.json and questions/sndk_pro.json in skills/sndk-jev, and the state files under state/jev/, {E(BUILT_AT)}. Every label sentence is from a real stored row except the news ones and the two momentum labels on a quiet read, which are example wording.</p>
</div>
"""
OUT.write_text(page, encoding="utf-8")
skeleton = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '<style>:root{color-scheme:light;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif;background:#fafafa}img{max-width:100%}[hidden]{display:none!important}</style></head><body>')
OUT.with_name(OUT.stem + "-preview.html").write_text(skeleton + page + "</body></html>", encoding="utf-8")
print(f"built {OUT} {len(page.encode('utf-8'))} bytes; {N_Q} questions in {N_G} groups, {N_Q_SENT} sent in {N_G_SENT} requests; labels {dict(STATUS_COUNT)}")
