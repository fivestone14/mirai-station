"""Build the JEV overlay page for SNDK PRO.

    python3 build_page.py OUT.html

Reads, all read-only:
  skills/sndk-jev/spec/labels.json         the label spec (built, free, new)
  skills/sndk-jev/questions/sndk_pro.json  the questions, with viewpoint, status, ask and why per question
  state/sndk_reversion/*.jsonl              to measure SNDK's own 60-minute base rates at the question bands
"""
from __future__ import annotations

import bisect
import glob
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

SKILL = Path.home() / ".claude/plugins/mirai-station/skills/sndk-jev"
STATE = Path.home() / ".claude/plugins/mirai-station/state"
OUT = sys.argv[1]

SPEC = json.load(open(SKILL / "spec" / "labels.json", encoding="utf-8"))
QDOC = json.load(open(SKILL / "questions" / "sndk_pro.json", encoding="utf-8"))
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
VP_SHORT = {"strikes": "strikes", "volume_price": "volume & price", "history": "yesterday", "indicators": "indicators",
            "space_time": "space & time", "framing": "framing", "news": "news", "outcome": "outcome"}
LABELS_BY_PATH = {l["path"]: l for l in SPEC["labels"]}
STATUS_COUNT = Counter(l["status"] for l in SPEC["labels"])
BUILT_PATHS = {l["path"] for l in SPEC["labels"] if l["status"] == "built"}

reads_of: dict[str, list[str]] = {}
for gid, qid, q in QS:
    for p in dict.fromkeys(PATH_RE.findall(json.dumps(q, ensure_ascii=False))):
        reads_of.setdefault(p, []).append(qid)


def read_by(path: str) -> list[str]:
    group = path.split(".")[0]
    return list(dict.fromkeys(reads_of.get(path, []) + reads_of.get(group, [])))


def q_status(q) -> str:
    if q.get("status"):
        return q["status"]
    paths = PATH_RE.findall(json.dumps(q, ensure_ascii=False))
    return "live" if all(p in BUILT_PATHS or p.split(".")[0] in {x.split(".")[0] for x in BUILT_PATHS} for p in paths) else "waiting"


def q_viewpoint(gid, q) -> str:
    return q.get("viewpoint") or ("outcome" if gid == "outcome_shadow" else gid)


# ------------------------------------------------------------------ SNDK base rates, measured at build time

def base_rates(flat: float, large: float, horizon_min: int = 30):
    parse = datetime.fromisoformat
    end_moves, max_exc, days = [], [], set()
    for rf in sorted(glob.glob(str(STATE / "sndk_reversion" / "2026-*.jsonl"))):
        rows = []
        for line in open(rf, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            s, sg, ts = r.get("spot"), r.get("sigma"), r.get("ts")
            if isinstance(s, (int, float)) and isinstance(sg, (int, float)) and sg and isinstance(ts, str):
                rows.append((parse(ts), float(s), float(sg)))
        if len(rows) < 60:
            continue
        rows.sort(key=lambda x: x[0])
        times = [x[0] for x in rows]
        for i, (t0, s0, sg0) in enumerate(rows):
            m = t0.hour * 60 + t0.minute
            if not (9 * 60 + 45 <= m <= 16 * 60 - horizon_min):
                continue
            t1 = t0 + timedelta(minutes=horizon_min)
            j = bisect.bisect_left(times, t1)
            if j >= len(rows) or (times[j] - t1) > timedelta(minutes=3):
                continue
            win = [x[1] for x in rows[i + 1:j + 1]]
            if len(win) < 15:
                continue
            end_moves.append((rows[j][1] - s0) / sg0)
            max_exc.append(max(abs(w - s0) for w in win) / sg0)
            days.add(Path(rf).stem)
    n = len(end_moves)
    if not n:
        return None
    sh = lambda f: 100.0 * sum(1 for x in end_moves if f(x)) / n
    ms = lambda f: 100.0 * sum(1 for x in max_exc if f(x)) / n
    return {
        "n": n, "days": len(days), "first": min(days), "last": max(days),
        "ladder": [sh(lambda x: x <= -large), sh(lambda x: -large < x <= -flat), sh(lambda x: abs(x) < flat),
                   sh(lambda x: flat <= x < large), sh(lambda x: x >= large)],
        "wander": [ms(lambda x: x < flat), ms(lambda x: flat <= x < large), ms(lambda x: x >= large)],
    }


C = QDOC.get("constants", {})
FLAT = float(C.get("flat_band_sigma", 0.12))
LARGE = float(C.get("large_move_sigma", 0.30))
BR = base_rates(FLAT, LARGE)

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
code,.mono{font-family:var(--mono)}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.wrap{max-width:1040px;margin:0 auto;padding-inline:clamp(16px,4vw,32px);padding-block:26px 72px}
.eyebrow{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center;font:500 12px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
h1{margin-top:14px;font-size:clamp(30px,5.4vw,44px);font-weight:700;letter-spacing:-.015em}
.purpose{margin-top:12px;color:var(--ink-2);font-size:18px;max-width:64ch}
.jump{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}
.chip{display:inline-flex;align-items:center;min-height:26px;padding:3px 10px;border:1px solid var(--line-strong);border-radius:999px;font:500 12px/1.1 var(--mono);color:var(--ink-2);background:var(--surface);text-decoration:none}
a.chip:hover{background:var(--surface-2);color:var(--ink)}
.chip.type{text-transform:uppercase;letter-spacing:.05em;font-size:11px;color:var(--ink);background:var(--surface-2)}
.tag{display:inline-block;font:500 10.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.05em;padding:4px 6px;border-radius:4px;background:var(--accent-soft);color:var(--accent)}
.st{display:inline-block;font:500 10.5px/1 var(--mono);text-transform:uppercase;letter-spacing:.05em;padding:4px 7px;border-radius:4px}
.st.built,.st.live{background:var(--good-bg);color:var(--good)}
.st.free,.st.waiting{background:var(--accent-soft);color:var(--accent)}
.st.new{background:var(--warn-bg);color:var(--warn)}
.st.shadow,.st.blocked{background:var(--surface-2);color:var(--ink-3);border:1px solid var(--line)}
.st.dark{background:var(--surface-2);color:var(--ink-3);border:1px dashed var(--line-strong)}
.sec{margin-top:48px}
.sec>h2{font-size:28px;letter-spacing:-.005em}
.sub{margin-top:8px;color:var(--ink-2);max-width:72ch;font-size:17px}
.k{font:500 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);margin-right:8px}

/* diagrams */
.fig{margin:16px 0 0}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--surface);box-shadow:var(--shadow)}
.scroll svg{display:block;width:100%;min-width:780px;height:auto;color:var(--ink-2)}
.dg-box{fill:var(--surface);stroke:var(--ink-3);stroke-width:1.4}
.dg-box.plan{stroke-dasharray:6 4}
.dg-box.miss{stroke:var(--bad);stroke-dasharray:2 4;stroke-width:1.8}
.dg-box.jev{fill:var(--accent-soft);stroke:var(--accent);stroke-width:2}
.dg-box.claude{fill:var(--claude-bg);stroke:var(--claude);stroke-width:1.6}
.dg-box.soft{fill:var(--surface-2)}
.dg-key{fill:none;stroke:var(--line-strong);stroke-width:1}
.dg-t{fill:var(--ink);font:600 12.5px var(--sans)}
.dg-s{fill:var(--ink-2);font:400 11.5px var(--sans)}
.dg-l{fill:var(--ink-3);font:500 11px var(--sans)}
.dg-tier{fill:var(--ink-3);font:500 10.5px var(--mono);letter-spacing:.08em}
.dg-arrow{stroke:currentColor;stroke-width:1.4;fill:none}
.dg-n{fill:var(--accent);font:600 11px var(--mono)}
figcaption{margin-top:12px;color:var(--ink-2);font-size:16px;max-width:92ch}

/* label map */
.vmap{margin-top:16px;display:grid;gap:10px}
.vrow{display:grid;gap:6px 14px;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--surface);align-items:center}
@media(min-width:760px){.vrow{grid-template-columns:170px minmax(0,1fr)}}
.vname{font-size:14px;font-weight:600}
.vname small{display:block;font:400 11.5px/1.4 var(--mono);color:var(--ink-3);margin-top:2px}
.lchips{display:flex;flex-wrap:wrap;gap:5px}
.lc{font:400 11.5px/1.3 var(--mono);padding:3px 8px;border-radius:5px;border:1px solid transparent;cursor:default}
.lc.built{background:var(--good-bg);color:var(--good)}
.lc.free{background:var(--accent-soft);color:var(--accent)}
.lc.new{background:var(--warn-bg);color:var(--warn)}
.lc.blocked{background:var(--surface-2);color:var(--ink-3);border-color:var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:12px;font-size:13px;color:var(--ink-2);align-items:center}
.legend .lc{cursor:default}
details.d{margin-top:12px}
details.d>summary{cursor:pointer;font:600 13.5px/1.3 var(--sans);color:var(--accent)}
.lb{display:grid;gap:6px 20px;padding:12px 14px;border-top:1px solid var(--line);background:var(--surface)}
@media(min-width:900px){.lb{grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr) minmax(0,1.2fr);align-items:start}}
.lbwrap{margin-top:10px;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.lbwrap .lb:first-child{border-top:0}
.lbh{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.lbh .qid{font:500 13px/1.3 var(--mono);color:var(--ink);overflow-wrap:anywhere}
.lbl{font-size:13.5px;color:var(--ink-2);margin-top:4px}
.lbr{font-size:14px;color:var(--ink);margin-top:4px}
.lbr q{quotes:"\201C" "\201D"}
.lbn{font-size:12.5px;color:var(--ink-3);margin-top:4px}

/* base rates */
.viz{margin:16px 0 0;padding:18px 20px 16px;background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow)}
.panels{display:grid;gap:22px}
@media(min-width:860px){.panels{grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:40px}}
.viz h3{font-size:16px}
.viz .cs{margin-top:3px;color:var(--ink-3);font-size:13px}
.rows{margin-top:12px;display:grid;gap:7px}
.lrow{display:grid;grid-template-columns:minmax(0,150px) minmax(0,1fr);gap:0 12px;align-items:center;min-height:24px}
.lab{font-size:13px;color:var(--ink-2);line-height:1.25}
.track{position:relative;height:20px}
.bar{position:absolute;left:0;top:1px;height:18px;min-width:4px;border-radius:0 4px 4px 0}
.bar.up{background:var(--up)}.bar.down{background:var(--down)}.bar.flat{background:var(--flat)}
.bar.w1{background:var(--w1)}.bar.w2{background:var(--w2)}.bar.w3{background:var(--w3)}
.val{position:absolute;top:0;line-height:20px;font:600 13px var(--sans);font-variant-numeric:tabular-nums;color:var(--ink);white-space:nowrap}
.note{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);font-size:13px;color:var(--ink-2)}

/* questions */
.vph{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:baseline;margin-top:34px;padding-bottom:8px;border-bottom:1px solid var(--line-strong)}
.vph h3{font-size:19px}
.vph .vc{font:400 12.5px/1.4 var(--mono);color:var(--ink-3)}
.vph .intu{font-size:13.5px;color:var(--ink-3);flex-basis:100%}
.qcards{display:grid;gap:10px;margin-top:12px}
@media(min-width:860px){.qcards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.qc{display:grid;gap:8px;padding:14px 16px;background:var(--surface);border:1px solid var(--line);border-radius:10px;align-content:start}
.qc.shadow{border-style:dashed}
.qtop{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.qtop .qid{font:500 12px/1.3 var(--mono);color:var(--ink-3);overflow-wrap:anywhere}
.ask{font-size:16px;font-weight:500;line-height:1.35;text-wrap:pretty}
.opts{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.opt{font-size:12.5px;line-height:1.3;padding:3px 8px;border-radius:6px;background:var(--surface-2);border:1px solid var(--line);color:var(--ink-2)}
.opt.esc{color:var(--ink-3);border-style:dashed}
.sep{color:var(--ink-3);font-size:13px}
.why{font-size:13.5px;color:var(--ink-2);line-height:1.4}
.why .k{margin-right:6px}
.reads{display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.reads .lc{font-size:11px;padding:2px 6px}

/* json */
.jtools{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;margin-top:12px}
.btn{border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);border-radius:8px;padding:9px 14px;font:500 15px/1 var(--sans);cursor:pointer}
.btn:hover{background:var(--surface-2)}
.stt{font-size:15px;color:var(--ink-3)}
.sg{margin-top:10px;border:1px solid var(--line);border-radius:10px;background:var(--surface);overflow:hidden}
.sg>summary{display:flex;align-items:center;gap:12px;padding:10px 14px;cursor:pointer;list-style:none}
.sg>summary::-webkit-details-marker{display:none}
.sg>summary::before{content:"\25B8";color:var(--ink-3);display:inline-block;transition:transform .12s}
.sg[open]>summary::before{transform:rotate(90deg)}
.sgid{font:500 14px/1.3 var(--mono)}
.sgc{font-size:12.5px;color:var(--ink-3)}
.sgcp{margin-left:auto;padding:5px 10px;font-size:12px}
.sgcode{border-top:1px solid var(--line);background:var(--surface-2);padding:12px 14px 14px;font:400 12.5px/1.6 var(--mono);color:var(--ink);overflow-x:auto}
.ln{overflow-wrap:anywhere}
.jk{color:var(--syn-key)}.js{color:var(--syn-str)}.jn{color:var(--syn-num)}.jl{color:var(--syn-lit)}
.foot{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);font-size:12.5px;color:var(--ink-3)}



/* schema rows */
.rvh{margin-top:22px;font-size:19px}
.rvl{margin-top:8px;display:grid;gap:6px}
.rv{display:grid;gap:4px 12px;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface)}
.rv .qid{font:500 14px/1.3 var(--mono);overflow-wrap:anywhere}
.rv .why{font-size:15px}
@media(min-width:760px){.rv{grid-template-columns:minmax(0,1fr) auto minmax(0,1.9fr);align-items:center}}


/* the weight log: a database table */
.tally{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.tl{display:flex;flex-direction:column;gap:2px;padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--surface);min-width:120px}
.tl .k{margin:0}
.tl b{font:600 20px/1.1 var(--sans);color:var(--ink)}
.tabbar{display:flex;gap:4px;margin-top:18px;border-bottom:1px solid var(--line-strong)}
.tabbtn{border:1px solid transparent;border-bottom:0;background:transparent;color:var(--ink-3);border-radius:8px 8px 0 0;padding:9px 14px;font:500 14px/1 var(--sans);cursor:pointer}
.tabbtn.on{background:var(--surface);border-color:var(--line-strong);color:var(--ink);margin-bottom:-1px;border-bottom:1px solid var(--surface)}
.tabpane{border:1px solid var(--line-strong);border-top:0;border-radius:0 0 10px 10px;background:var(--surface);overflow-x:auto}
table.db{width:100%;border-collapse:collapse;font:400 14px/1.4 var(--mono);min-width:720px}
table.db th{position:sticky;top:0;background:var(--surface-2);color:var(--ink-3);font:500 11.5px/1.2 var(--mono);letter-spacing:.05em;text-transform:uppercase;text-align:left;padding:10px 12px;border-bottom:1px solid var(--line-strong)}
table.db td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top;color:var(--ink)}
table.db tbody tr:nth-child(even) td{background:var(--surface-2)}
table.db .num{text-align:right;font-variant-numeric:tabular-nums}
table.db td.mono{white-space:nowrap}
table.db td.empty{color:var(--ink-3);font:400 15px/1.5 var(--sans);padding:22px 14px}


table.db.wt{min-width:0;font-family:var(--sans);font-size:15px}
table.db.wt td.ask{width:58%}
table.db.wt td.ask code{display:block;font:400 11.5px/1.4 var(--mono);color:var(--ink-3);margin-top:2px}
table.db.wt td.w{width:26%;vertical-align:middle}
.wbar{height:10px;border-radius:5px;background:var(--code-bg);overflow:hidden}
.wbar i{display:block;height:10px;background:var(--accent);border-radius:5px}
table.db.wt tr.out td{color:var(--ink-3)}
table.db.wt tr.out .wbar i{background:var(--bad)}

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

/* phone card mock */
.phone{margin:16px auto 0;max-width:390px;border:1px solid var(--line-strong);border-radius:24px;padding:16px 16px 18px;background:var(--surface);box-shadow:var(--shadow)}
.ph-head{display:flex;gap:10px;align-items:baseline;font-size:15px}
.ph-head b{font-size:19px}
.ph-head .ph-age{margin-left:auto;font:400 12px var(--mono);color:var(--ink-3)}
.ph-state{margin-top:6px;font:500 11px/1 var(--mono);text-transform:uppercase;letter-spacing:.05em;color:var(--warn);background:var(--warn-bg);display:inline-block;padding:4px 7px;border-radius:4px}
.ph-sit{margin-top:12px}
.ph-sit ul{display:grid;gap:4px;font-size:13px;color:var(--ink-2);line-height:1.35}
.ph-sit li{padding-left:10px;border-left:2px solid var(--line-strong)}
.ph-vp{margin-top:14px;font:500 11px/1 var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ph-vp small{font-size:11px;color:var(--ink-3)}
.ph-q{margin-top:8px;padding:9px 10px;border:1px solid var(--line);border-radius:10px}
.ph-q.shadow{border-style:dashed}
.ph-ask{font-size:13.5px;font-weight:500;line-height:1.3}
.ph-bar{display:grid;grid-template-columns:minmax(0,92px) minmax(0,1fr) 38px;gap:8px;align-items:center;margin-top:5px;font-size:12px;color:var(--ink-2)}
.ph-bar i{display:block;height:8px;border-radius:4px;background:var(--accent)}
.ph-bar b{text-align:right;font-variant-numeric:tabular-nums}
.ph-skip{margin-top:5px;font:400 11.5px var(--mono);color:var(--ink-3)}
.ph-foot{margin-top:14px;font-size:12px;color:var(--ink-3)}
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


def key(x, y, items):
    s = f'<rect class="dg-key" x="{x}" y="{y}" width="222" height="{26 + 26 * len(items)}" rx="7"/>'
    s += f'<text class="dg-tier" x="{x + 14}" y="{y + 20}">KEY</text>'
    for i, (cls, txt) in enumerate(items):
        yy = y + 32 + i * 26
        s += f'<rect class="dg-box {cls}" x="{x + 14}" y="{yy}" width="30" height="14" rx="3" style="stroke-width:1.4"/>'
        s += f'<text class="dg-s" x="{x + 56}" y="{yy + 11}">{E(txt)}</text>'
    return s


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
    p.append(box(128, 118, 390, 62, "SNDK PRO scan, every 120 s", ["one diary row: gamma map, walls, IV, contracts"]))
    p.append(box(534, 118, 180, 62, "Bar sidecar", ["keeps the minute bars"]))
    p.append(box(730, 118, 212, 62, "The Reader (Claude)", ["one call, its notes, unchanged"], "claude"))
    # mirai files
    p.append(box(128, 208, 814, 60, "Mirai's state files, never written by JEV",
                 ["diary rows every 120 s, minute bars, side packets, the chain cache, the Reader's notes"]))
    # the service lane
    p.append('<rect class="dg-key" x="118" y="292" width="834" height="196" rx="9" style="stroke-dasharray:7 5;stroke:var(--accent)"/>')
    p.append('<text class="dg-tier" x="300" y="312" style="fill:var(--accent)">JEV DECISION SERVICE: ITS OWN JOB EVERY 120 S; READS MIRAI\'S FILES, WRITES ONLY state/jev/</text>')
    y = 332
    p.append(box(128, y, 150, 84, "Scene loader", ["newest row, finished bars,", "prior days, side packet,", "chain cache; no look-ahead"]))
    p.append(box(294, y, 170, 84, "State builder", [f"{STATUS_COUNT['built']} labels in sigma,", "the cut written into", "every sentence"]))
    p.append(box(480, y, 130, 84, "Packer", [f"{N_G} requests, one", "per question group;", "no label, no question"]))
    p.append(box(626, y, 170, 84, "JEV, the fast judge", [f"{n_live} live + {n_shadow} shadow", "questions in parallel, then", "one question sums the answers"], "jev"))
    p.append(box(812, y, 130, 84, "Writer, grader", ["{day}.jsonl, hour/, grades,", "weights, latest.json"]))
    for x0 in (278, 464, 610, 796):
        p.append(arrow([(x0, y + 42), (x0 + 16, y + 42)]))
    # served
    p.append(box(128, 520, 300, 62, "Viewstation :8787, unchanged", ["already serves state files read-only", "over /api/raw/file; no new route"]))
    p.append(box(460, 520, 290, 62, "state/jev/latest.json", ["the newest run: situation, answers,", "freshness, what was not sent"]))
    p.append(box(780, 520, 162, 62, "ntfy push", ["later: when an", "answer flips"], "plan"))
    # phone
    p.append(box(128, 614, 300, 66, "/m/jev.html, the decision card", ["polls every 60 s; situation, each answer", "as probabilities, shadow marked, age shown"]))
    p.append(box(460, 614, 290, 66, "/m glance", ["the Reader's notes, unchanged"]))
    p.append(box(780, 614, 162, 66, "Nightly grading", ["later: the bars grade", "the shadow answers"], "plan"))
    # arrows
    p.append(arrow([(253, 92), (253, 118)], "price, bars", 261, 110))
    p.append(arrow([(519, 92), (519, 118)], "chain", 527, 110))
    p.append(arrow([(323, 180), (323, 208)], "row every 120 s", 331, 200))
    p.append(arrow([(624, 180), (624, 208)], "minute bars", 632, 200))
    p.append(arrow([(836, 180), (836, 208)], "its notes", 844, 200))
    p.append(arrow([(203, 268), (203, 332)], "reads, never writes", 211, 300))
    p.append(arrow([(877, 416), (877, 502), (605, 502), (605, 520)], "latest.json, every run", 700, 496, anchor="middle"))
    p.append(arrow([(460, 551), (428, 551)], "read-only", 444, 545, anchor="middle"))
    p.append(arrow([(278, 582), (278, 614)], "GET every 60 s", 286, 604))
    p.append(arrow([(400, 582), (400, 598), (605, 598), (605, 614)], "the notes", 613, 610))
    return ('<svg viewBox="0 0 960 692" role="img" aria-label="How the JEV decision feature sits beside Mirai station: Schwab and ThetaData feed the SNDK PRO scan and the bar sidecar, which write Mirai\'s state files; the JEV decision service is its own job that reads those files, builds the labels, asks JEV and writes state/jev/latest.json; the unchanged viewstation serves that file read-only and the phone\'s decision card polls it; push and grading come later.">'
            + "".join(p) + "</svg>")


# ------------------------------------------------------------------ diagram 2: the state builder, in detail

def state_diagram():
    p = [DEFS]
    for label, y in [("INPUTS", 62), ("SCENE", 175), ("LABELLER", 282), ("", 0), ("STATE", 478), ("PACKER", 598), ("JEV", 690)]:
        if label:
            p.append(f'<text class="dg-tier" x="12" y="{y}">{label}</text>')
    # inputs
    p.append(box(128, 24, 190, 68, "Diary row", ["spot, sigma, vwap, IV, gamma map,", "walls, contracts per strike"]))
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
                 ["0.15 sigma move rule, 0.5 sigma wall, top and bottom fifth, 2 vol points; the cut is in the words, so JEV never compares"]))
    p.append(arrow([(758, 379), (786, 379)]))
    p.append(box(788, 348, 154, 62, "Cannot measure it?", ["leave the label out", "and record why"], "plan"))
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
    p.append(arrow([(535, 630), (535, 660)], f"{N_G} requests, in parallel", 545, 648))
    p.append(box(128, 662, 814, 58, "JEV", [f"{N_Q} questions answered at once; a probability per answer option; about 100 ms"], "jev"))
    return ('<svg viewBox="0 0 960 744" role="img" aria-label="The state builder in detail: four inputs, the moment is pinned with no look-ahead, four transforms turn numbers into sentences with the cut written in, unmeasurable labels are left out, the labels group into seven viewpoints, the packer cuts one request per group, and JEV answers in parallel.">'
            + "".join(p) + "</svg>")


# ------------------------------------------------------------------ label map (visual) + details

def lchip(l):
    return f'<span class="lc {l["status"]}" title="{E(l["reads_as"])}">{E(l["path"].split(".", 1)[1])}</span>'


def label_detail(l):
    rb = read_by(l["path"])
    chips = "".join(f'<span class="lc built" style="font-size:11px">{E(q)}</span>' for q in rb) if rb else '<span class="lbn">no question reads it yet</span>'
    ex = "" if l.get("real") else ' <span class="tag">example wording</span>'
    note = f'<div class="lbn">{E(l["note"])}</div>' if l.get("note") else ""
    return (f'<div class="lb"><div><div class="lbh"><code class="qid">{E(l["path"])}</code><span class="st {l["status"]}">{l["status"]}</span><span class="chip type">{E(l["type"])}</span></div>{note}</div>'
            f'<div><div class="lbl"><span class="k">From</span>{E(l["from"])}</div><div class="lbl"><span class="k">Logic</span>{E(l["logic"])}</div><div class="lbl"><span class="k">Cut</span>{E(l["cut"])}</div></div>'
            f'<div><div class="lbr"><span class="k">Reads as</span><q>{E(l["reads_as"])}</q>{ex}</div><div class="reads" style="margin-top:6px"><span class="k">Read by</span>{chips}</div></div></div>')


def label_map():
    rows = []
    for vp in SPEC["viewpoints"]:
        labs = [l for l in SPEC["labels"] if l["viewpoint"] == vp["id"]]
        c = Counter(l["status"] for l in labs)
        meta = " · ".join(f"{n} {s}" for s, n in c.items())
        rows.append(f'<div class="vrow"><div class="vname">{E(vp["title"])}<small>{E(meta)}</small></div><div class="lchips">{"".join(lchip(l) for l in labs)}</div></div>')
    details = []
    for vp in SPEC["viewpoints"]:
        labs = [l for l in SPEC["labels"] if l["viewpoint"] == vp["id"]]
        details.append(f'<details class="d"><summary>{E(vp["title"])}: the logic behind each label</summary>'
                       f'<p class="sub" style="margin-top:8px;font-size:13.5px">{E(vp["review"])}</p>'
                       f'<div class="lbwrap">{"".join(label_detail(l) for l in labs)}</div></details>')
    folded = SPEC.get("folded", [])
    fold = ("".join(f'<div class="lbn" style="margin-top:8px"><code class="qid">{E(f["path"])}</code> folded into <code class="qid">{E(f["into"])}</code> on {E(f["date"])}: {E(f["why"])}</div>' for f in folded))
    return ('<div class="vmap">' + "".join(rows) + '</div>'
            '<div class="legend"><span class="lc built">built</span><span>writing sentences today; hover a label for its real sentence</span></div>'
            + fold + "".join(details))



# ------------------------------------------------------------------ base-rate chart

def lrow(label, val, factor, cls):
    f = f"{max(0.0, min(1.0, factor)):.4f}"
    return (f'<div class="lrow"><div class="lab">{E(label)}</div><div class="track"><div class="bar {cls}" style="width:calc((100% - 56px) * {f})"></div>'
            f'<span class="val" style="left:calc((100% - 56px) * {f} + 8px)">{val:.1f}%</span></div></div>')


def chart():
    if not BR:
        return '<p class="sub">No stored SNDK rows found, so no base rates.</p>'
    L, W = BR["ladder"], BR["wander"]
    lad = [(f"Up over {LARGE:g}σ", L[4], "up"), (f"Up {FLAT:g} to {LARGE:g}σ", L[3], "up"), (f"Flat, within {FLAT:g}σ", L[2], "flat"),
           (f"Down {FLAT:g} to {LARGE:g}σ", L[1], "down"), (f"Down over {LARGE:g}σ", L[0], "down")]
    wan = [(f"Within {FLAT:g}σ", W[0], "w1"), (f"{FLAT:g} to {LARGE:g}σ", W[1], "w2"), (f"Over {LARGE:g}σ", W[2], "w3")]
    mx = max(max(x[1] for x in lad), 1)
    mw = max(max(x[1] for x in wan), 1)
    return f"""
<figure class="viz"><div class="panels">
  <div><h3>Where SNDK ends up 30 minutes later</h3><p class="cs">Share of scans, at the bands the questions use. The side is the direction, the distance from flat is the size.</p>
    <div class="rows" role="img" aria-label="Distribution of SNDK's 60-minute outcome">{"".join(lrow(l, v, v / mx, c) for l, v, c in lad)}</div></div>
  <div><h3>How far it wanders in the 30 minutes</h3><p class="cs">Furthest point reached, either way.</p>
    <div class="rows" role="img" aria-label="Distribution of the furthest distance reached">{"".join(lrow(l, v, v / mw, c) for l, v, c in wan)}</div></div>
</div>
<p class="note">Measured at build time from SNDK PRO's stored scans: {BR['n']:,} windows on {BR['days']} sessions, {BR['first']} to {BR['last']}, starting between 09:45 and 15:30 ET. Up {L[3] + L[4]:.1f}%, down {L[0] + L[1]:.1f}%, so the base rate gives no lean. σ is the day's expected move.</p>
</figure>"""


# ------------------------------------------------------------------ questions

def options_html(q):
    t = q["type"]
    crit = q["criteria"]
    if t == "noul":
        return '<div class="opts"><span class="opt">yes</span><span class="opt">no</span></div>'
    if t == "score":
        sep = '<span class="sep" aria-hidden="true">›</span>'
        return '<div class="opts">' + sep.join(f'<span class="opt">{E(c)}</span>' for c in crit) + '</div>'
    parts = []
    for k in crit:
        parts.append(f'<span class="opt{" esc" if k == "unsure" else ""}">{E(k.replace("_", " "))}</span>')
    return '<div class="opts">' + "".join(parts) + '</div>'


def qcard(gid, qid, q):
    st = q_status(q)
    ask = q.get("ask") or q["instructions"]
    why = q.get("why", "")
    reads = [p for p in dict.fromkeys(PATH_RE.findall(json.dumps(q, ensure_ascii=False))) if not p.startswith("context.")]
    rchips = "".join(f'<span class="lc {LABELS_BY_PATH[p]["status"] if p in LABELS_BY_PATH else "built"}">{E(p)}</span>' for p in reads)
    cad = f'<span class="chip" style="font-size:11px">{E(q["cadence"])}</span>' if q.get("cadence") else ""
    return (f'<div class="qc {st}"><div class="qtop"><span class="st {st}">{st}</span><span class="chip type">{E(q["type"])}</span>{cad}<code class="qid">{E(qid)}</code></div>'
            f'<p class="ask">{E(ask)}</p>{options_html(q)}'
            + (f'<p class="why"><span class="k">Why</span>{E(why)}</p>' if why else "")
            + (f'<div class="reads"><span class="k">Reads</span>{rchips}</div>' if rchips else "")
            + '</div>')


def questions_section():
    by_vp: dict[str, list] = {}
    for gid, qid, q in QS:
        by_vp.setdefault(q_viewpoint(gid, q), []).append((gid, qid, q))
    order = [v for v in VP_ORDER if v in by_vp] + [v for v in by_vp if v not in VP_ORDER]
    intu = {v["id"]: v["intuition"] for v in SPEC["viewpoints"]}
    out = []
    for vp in order:
        items = by_vp[vp]
        c = Counter(q_status(q) for _, _, q in items)
        meta = " · ".join(f"{n} {s}" for s, n in c.items())
        out.append(f'<div class="vph"><h3>{E(VP_TITLE.get(vp, vp))}</h3><span class="vc">{len(items)} questions · {E(meta)}</span>'
                   + (f'<span class="intu">“{E(intu[vp])}”</span>' if vp in intu and vp not in ("framing", "news") else "")
                   + '</div><div class="qcards">' + "".join(qcard(*it) for it in items) + '</div>')
    return "".join(out)


# ------------------------------------------------------------------ json for jev

def highlight(text):
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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


def jev_only(q):
    return {k: q[k] for k in ("type", "instructions", "criteria") if k in q}


def ln_html(obj):
    rows = []
    for line in json.dumps(obj, indent=2, ensure_ascii=False).split("\n"):
        s = line.lstrip(" ")
        ind = len(line) - len(s)
        rows.append(f'<div class="ln" style="padding-left:{ind + 4}ch;text-indent:-4ch">{highlight(s)}</div>')
    return "".join(rows)


def json_section():
    groups = []
    data = {}
    for g in QDOC["groups"]:
        qs = {qid: jev_only(q) for qid, q in g["questions"].items()}
        data[g["id"]] = {"state_slice": g["reads"], "questions": qs}
        groups.append(f'<details class="sg" id="sg-{g["id"]}"><summary><span class="sgid">{E(g["id"])}</span><span class="sgc">{len(qs)} question{"s" if len(qs) != 1 else ""} · reads {E(", ".join(g["reads"]))}</span>'
                      f'<button class="btn sgcp" type="button" data-g="{g["id"]}">Copy</button></summary><div class="sgcode">{ln_html(qs)}</div></details>')
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    js = r"""
(function(){
  var data = JSON.parse(document.getElementById('jev-data').textContent);
  var st = document.getElementById('jstat');
  function say(m){ st.textContent = m; setTimeout(function(){ st.textContent = ''; }, 2200); }
  function copy(t){ try { navigator.clipboard.writeText(t).then(function(){ say('Copied'); }, function(){ say('Copy is blocked here'); }); } catch (e) { say('Copy is blocked here'); } }
  document.querySelectorAll('.sgcp').forEach(function(b){ b.addEventListener('click', function(e){ e.preventDefault(); e.stopPropagation(); copy(JSON.stringify(data[b.getAttribute('data-g')].questions, null, 2)); }); });
  document.getElementById('jall').addEventListener('click', function(){ copy(JSON.stringify(data, null, 2)); });
  function all(o){ document.querySelectorAll('details.sg').forEach(function(d){ d.open = o; }); }
  document.getElementById('jex').addEventListener('click', function(){ all(true); });
  document.getElementById('jco').addEventListener('click', function(){ all(false); });
})();
"""
    return ('<div class="jtools"><button class="btn" type="button" id="jex">Expand all</button><button class="btn" type="button" id="jco">Collapse all</button><button class="btn" type="button" id="jall">Copy all requests</button><span class="stt" id="jstat"></span></div>'
            + "".join(groups) + f'<script type="application/json" id="jev-data">{blob}</script><script>{js}</script>')


# ------------------------------------------------------------------ review and phone

def review_section():
    r = SPEC.get("review")
    if not r:
        return ""
    def row(head, badge, text):
        return f'<div class="rv"><code class="qid">{E(head)}</code><span class="opt">{E(badge)}</span><span class="why">{E(text)}</span></div>'
    one = "".join(row(o["path"], o["top"], o["kept_as"]) for o in r["one_sided"])
    ov = "".join(row(o["pair"][0] + "  +  " + o["pair"][1], f"NMI {o['nmi']:.2f}" + (f", agree {o['agree']}" if o.get("agree") else ""), o["decision"]) for o in r["overlap_found"])
    fo = "".join(row(f["path"], "folded into " + f["into"], f["why"]) for f in SPEC.get("folded", []))
    return (f'<p class="sub">{E(r["method"])}</p>'
            f'<h3 class="rvh">Labels that nearly always say the same thing</h3><div class="rvl">{one}</div>'
            f'<h3 class="rvh">Pairs that move together</h3><div class="rvl">{ov}</div>'
            f'<h3 class="rvh">Folded, so JEV never reads one fact twice</h3><div class="rvl">{fo}</div>'
            f'<p class="sub" style="margin-top:12px;font-size:13px">{E(r["not_measurable"])}</p>')


def phone_section():
    f = STATE / "jev" / "latest.json"
    if not f.exists():
        return '<p class="sub">No card written yet. Run python3 -m sndk_jev.service once and rebuild.</p>'
    c = json.load(open(f, encoding="utf-8"))
    sit = "".join(f"<li>{E(x)}</li>" for x in c["situation"])
    by_vp: dict[str, list] = {}
    for q in c["questions"]:
        by_vp.setdefault(q.get("viewpoint") or "outcome", []).append(q)
    rows = []
    per = 2
    for vp in [v for v in VP_ORDER if v in by_vp]:
        rows.append(f'<div class="ph-vp">{E(VP_SHORT.get(vp, vp))} <small>{len(by_vp[vp])}</small></div>')
        for q in by_vp[vp][:per]:
            a = q.get("answer")
            if a and a.get("probabilities"):
                bars = "".join(f'<div class="ph-bar"><span>{E(k.replace("_", " "))}</span><i style="width:{v * 100:.0f}%"></i><b>{v * 100:.0f}%</b></div>'
                               for k, v in sorted(a["probabilities"].items(), key=lambda kv: -kv[1]))
            elif a and a.get("noul") is not None:
                bars = f'<div class="ph-bar"><span>yes</span><i style="width:{a["noul"] * 100:.0f}%"></i><b>{a["noul"] * 100:.0f}%</b></div>'
            else:
                bars = f'<div class="ph-skip">{E(q.get("skipped", "not asked"))}</div>'
            rows.append(f'<div class="ph-q{" shadow" if q.get("status") == "shadow" else ""}"><div class="ph-ask">{E(q["ask"])}</div>{bars}</div>')
    n_sent = sum(1 for q in c["questions"] if q.get("answer"))
    row_t = c["row_ts"][11:16]
    state = f"{n_sent} answered" if n_sent else "not sent: no key on this machine yet"
    return (f'<div class="phone"><div class="ph-head"><b>{E(c["symbol"])}</b><span>JEV</span><span class="ph-age">row {E(row_t)}, {c["labels"]} labels</span></div>'
            f'<div class="ph-state">{E(state)}</div>'
            f'<div class="ph-sit"><div class="ph-vp">situation</div><ul>{sit}</ul></div>'
            + "".join(rows)
            + f'<div class="ph-foot">showing {per} questions per viewpoint of {len(c["questions"])}. Shadow questions are forecasts graded later, never a call.</div></div>')


# ------------------------------------------------------------------ label to question mapping

Q_BY_ID = {qid: (gid, q) for gid, qid, q in QS}


def q_reads(qid):
    return [p for p in dict.fromkeys(PATH_RE.findall(json.dumps(Q_BY_ID[qid][1], ensure_ascii=False))) if not p.startswith("context.")]


def qmini(qid):
    gid, q = Q_BY_ID[qid]
    st = q_status(q)
    ask = q.get("ask") or q["instructions"]
    crit = q["criteria"]
    opts = ["yes", "no"] if q["type"] == "noul" else [c.replace("_", " ") for c in crit]
    if q["type"] == "score":
        opts = [c for c in crit]
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

HOUR_DOC = json.load(open(SKILL / "questions" / "sndk_hour.json", encoding="utf-8"))
LATEST = json.load(open(STATE / "jev" / "latest.json", encoding="utf-8")) if (STATE / "jev" / "latest.json").exists() else None
HOUR_LINE = None
if (STATE / "jev" / "hour").exists():
    files = sorted((STATE / "jev" / "hour").glob("*.jsonl"))
    if files:
        lines = [l for l in files[-1].read_text(encoding="utf-8").splitlines() if l.strip()]
        HOUR_LINE = json.loads(lines[-1]) if lines else None


def pipeline_diagram():
    p = [DEFS]
    steps = [
        ("1  Labels", "code", ["61 sentences from the", "row, bars, side packet"], ""),
        ("2  Thirty questions", "JEV, in parallel", ["each asked when its cadence", "is due, else its last answer held"], "jev"),
        ("3  Answers as words", "code", ["'...? rising, 100% sure'", "weighted, some left out"], ""),
        ("4  Two sums", "JEV", ["price in 30 and 60 min:", "Up, Down, Flat, Unsure"], "jev"),
        ("5  The card", "file, phone", ["state/jev/latest.json", "/m/jev.html polls it"], ""),
    ]
    x = 128
    for i, (title, who, subs, cls) in enumerate(steps):
        p.append(box(x, 40, 150, 96, title, [who] + subs, cls))
        if i < len(steps) - 1:
            p.append(arrow([(x + 150, 88), (x + 166, 88)]))
        x += 166
    p.append(box(294, 200, 340, 84, "6  Grading", ["code, every run, finished hours only", "close 60 min later vs spot, in sigma", "hit and Brier score per hour"], "plan"))
    p.append(box(650, 200, 292, 84, "Weights", ["each question's weight from how", "well its picks track the outcome;", "1.0 until 40 graded, cut at 0.5"], "plan"))
    p.append(arrow([(866, 136), (866, 170), (452, 170), (452, 200)], "the pick, spot, sigma, what was used", 560, 190))
    p.append(arrow([(634, 242), (650, 242)]))
    p.append(arrow([(796, 284), (796, 312), (535, 312), (535, 136)], "which answers speak on the next run", 545, 328, dashed=True))
    p.append('<text class="dg-tier" x="12" y="92">RUN</text><text class="dg-tier" x="12" y="246">LOOP</text>')
    return ('<svg viewBox="0 0 960 340" role="img" aria-label="The six-step pipeline: labels, thirty questions, answers as sentences, one summing question, the card; grading feeds weights back into which answers are used.">'
            + "".join(p) + "</svg>")


def schema_section():
    hq = HOUR_DOC["questions"]
    hour_json = {"id": "hour", "state": {"context": {"symbol": "SNDK", "horizon": "the next 30 minutes, and the next 60 minutes", "units": "..."},
                                        "answers": {"price_recent_direction": "Over the last 30 minutes, did price rise, fall, or go nowhere? rising, JEV was 100% sure",
                                                    "volume_now": "Is trading heavy, normal, or light for this time of day? normal, JEV was 98% sure",
                                                    "...": "one sentence per answered live question, 30 today"}},
                 "questions": {qid: {k: q[k] for k in ("type", "instructions", "criteria")} for qid, q in hq.items()}}
    real = ""
    if HOUR_LINE:
        real = {"row_ts": HOUR_LINE["row_ts"], "spot": "...", "sigma": HOUR_LINE["sigma"], "pick": HOUR_LINE.get("pick"),
                "probabilities": HOUR_LINE.get("probabilities"), "confidence": HOUR_LINE.get("confidence"),
                "used": {k: HOUR_LINE["used"][k] for k in list(HOUR_LINE["used"])[:3]} | {"...": f"{len(HOUR_LINE['used'])} in all"},
                "left_out": HOUR_LINE.get("left_out"), "sentences": "...", "request": "..."}
    files = [
        ("state/jev/{day}.jsonl", "every run", "row_ts, sigma, state (the labels), omitted, requests, skipped, held {qid: since}, cadence_from, answers (JEV's raw replies), hour"),
        ("state/jev/latest.json", "the phone's card", "symbol, row_ts, freshness, situation (5 plain lines), questions[] with answer or skipped, hour {pick, probabilities, confidence, used, left_out}"),
        ("state/jev/hour/{day}.jsonl", "what step 6 grades", "row_ts, spot, sigma, pick, probabilities, used {qid: pick}, left_out {qid: why}, sentences, request"),
        ("state/jev/grades.jsonl", "one line per graded read", "row_ts, then per sum: realized_sigma, band, pick, hit, brier; used"),
        ("state/jev/cadence.json", "what the packer reads", "recounted_from (the day), questions {qid: {minutes 30/60/120, p25_hold_min, changes, reads, why}}"),
        ("state/jev/last_asked.json", "the held answers", "qid: {row_ts of the last fresh answer, answer}; a not-due question is served from here, tagged held from HH:MM"),
        ("state/jev/weights.json", "what step 3 reads", "graded_runs, sum {hit_rate, always_flat_hit_rate, mean_brier}, questions {qid: {weight, mi, n, in_step_3, why}}"),
    ]
    rows = "".join(f'<div class="rv"><code class="qid">{E(a)}</code><span class="opt">{E(b)}</span><span class="why">{E(c)}</span></div>' for a, b, c in files)
    grade = """<div class="rvl">
<div class="rv"><code class="qid">realized</code><span class="opt">number</span><span class="why">(close 30 minutes after the row minus spot at the row) divided by sigma; the 60-minute sum is graded the same way at 60</span></div>
<div class="rv"><code class="qid">band</code><span class="opt">up / flat / down</span><span class="why">up above +0.12, down below -0.12, flat between at 30 minutes (0.17 at 60); the same bands the questions' criteria carry, measured on this name</span></div>
<div class="rv"><code class="qid">hit</code><span class="opt">yes / no</span><span class="why">JEV's pick equals the band</span></div>
<div class="rv"><code class="qid">brier</code><span class="opt">0 best, 2 worst</span><span class="why">sum over up, flat, down of (probability minus what happened)²; compared with always saying flat</span></div>
<div class="rv"><code class="qid">weight per question</code><span class="opt">0 to 1</span><span class="why">mutual information between what the question picked and the band, over the best question's; stays 1.0 until 40 graded runs; under 0.5 its sentence is left out of step 3</span></div>
<div class="rv"><code class="qid">not graded</code><span class="opt">skipped</span><span class="why">a horizon that runs past the close, or a day with no bars</span></div>
<div class="rv"><code class="qid">cadence</code><span class="opt">30 / 60 / 120 min</span><span class="why">recounted once a day from the previous day's runs: the 25th percentile of how long each question's answer held, halved and snapped; never changed all day gives 60 (three hours of runs) or 120 (five hours, ten reads); under six reads keeps the last value</span></div>
<div class="rv"><code class="qid">held</code><span class="opt">reused answer</span><span class="why">a question not yet due, or whose label is missing this read, keeps its last fresh answer for up to twice its cadence (never under an hour), tagged with the time it was given, on the card and in the sums' sentences</span></div>
</div>"""
    return (f'<h3 class="rvh">Files, all under state/jev/, written only by the service</h3><div class="rvl">{rows}</div>'
            f'<h3 class="rvh">How an hour is graded, step 6</h3>{grade}'
            f'<h3 class="rvh">The one request of step 4, as sent: two sums on the same sentences</h3><div class="sgcode" style="border:1px solid var(--line);border-radius:10px">{ln_html(hour_json)}</div>'
            + (f'<h3 class="rvh">The real hour record from the last run</h3><div class="sgcode" style="border:1px solid var(--line);border-radius:10px">{ln_html(real)}</div>' if real else ""))


# ------------------------------------------------------------------ the weight log, drawn from state/jev at build time

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


WLOG = _jsonl(STATE / "jev" / "weights_log.jsonl")
GRADES = _jsonl(STATE / "jev" / "grades.jsonl")
WEIGHTS = json.load(open(STATE / "jev" / "weights.json", encoding="utf-8")) if (STATE / "jev" / "weights.json").exists() else {}
CADENCE = json.load(open(STATE / "jev" / "cadence.json", encoding="utf-8")) if (STATE / "jev" / "cadence.json").exists() else {}
DOC_CADENCE = {"every scan": 30, "every 10 minutes": 30, "every 20 minutes": 30, "every 30 minutes": 30, "hourly": 60}


def cadence_min(qid, q):
    m = ((CADENCE.get("questions") or {}).get(qid) or {}).get("minutes")
    return int(m) if isinstance(m, (int, float)) else DOC_CADENCE.get(str(q.get("cadence", "")).lower(), 30)

BUILT_AT = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")


def _num(x, nd=3):
    return "" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _clock(iso):
    return E(str(iso)[:16].replace("T", " "))


def weight_log_section():
    """Just the weight per question, grouped by viewpoint. 1.0 until graded; under 0.5 leaves the sum."""
    wq = WEIGHTS.get("questions", {})
    graded = WEIGHTS.get("graded_runs", 0)
    need = WEIGHTS.get("min_graded", 40)
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
             if top else '<div class="tl"><span class="k">most adjusted</span><b>none yet</b><span class="k" style="text-transform:none;letter-spacing:0">no weight has moved: 0 graded reads of the 40 needed</span></div>')
    cad_note = (" (from " + E(CADENCE["recounted_from"]) + ")") if CADENCE.get("recounted_from") else ", not yet recounted so every question is on its starting value"
    out = [f'<h3 class="rvh" style="margin-top:8px">Most adjusted questions, total movement so far</h3><div class="tally">{strip}</div>',
           f'<p class="sub">Every live question starts at 1.0. After {need} graded reads a weight becomes how well that question\'s answers tracked the next 30 minutes, 1.0 for the best; under 0.5 the question stops feeding the sums. Graded reads so far: <b>{graded}</b>. Cadence is how often the question is asked afresh, recounted each day from the day before{cad_note}.</p>']
    for vp in [v for v in VP_ORDER if v in by_vp]:
        rows = []
        for qid, q in by_vp[vp]:
            w = wq.get(qid, {})
            weight = float(w.get("weight", 1.0))
            n = w.get("n", 0)
            cls = "out" if weight < 0.5 else ""
            rows.append(f'<tr class="{cls}"><td class="ask">{E(q.get("ask") or q["instructions"])}<code>{E(qid)}</code></td>'
                        f'<td class="w"><div class="wbar"><i style="width:{weight * 100:.0f}%"></i></div></td>'
                        f'<td class="num">{weight:.2f}</td><td class="num">{moved.get(qid, 0.0):.2f} <small>({moves.get(qid, 0)})</small></td><td class="num">{n}</td>'
                        f'<td class="num">{"every read" if cadence_min(qid, q) == 30 else str(cadence_min(qid, q)) + " min"}</td></tr>')
        out.append(f'<h3 class="rvh">{E(VP_TITLE.get(vp, vp))}</h3>'
                   f'<table class="db wt"><thead><tr><th>question</th><th>weight</th><th class="num">value</th><th class="num">moved (times)</th><th class="num">graded reads</th><th class="num">cadence</th></tr></thead>'
                   f'<tbody>{"".join(rows)}</tbody></table>')
    out.append(f'<p class="sub" style="margin-top:12px;font-size:14px">A snapshot of state/jev/weights.json on the station, taken when this page was built, {E(BUILT_AT)}. Every change is also kept in weights_log.jsonl.</p>')
    return "".join(out)


# ------------------------------------------------------------------ page

n_live = sum(1 for _, _, q in QS if q_status(q) == "live")
n_wait = sum(1 for _, _, q in QS if q_status(q) == "waiting")
n_shadow = sum(1 for _, _, q in QS if q_status(q) == "shadow")
n_dark = sum(1 for _, _, q in QS if q_status(q) == "dark")

page = f"""<title>JEV Direction Overlay</title>
<style>{CSS}</style>
<div class="wrap">
  <header>
    <div class="eyebrow"><span>SNDK PRO</span><span>questions {E(QDOC.get("version", ""))}</span><span>labels {E(SPEC.get("version", ""))}</span><span>{E(QDOC.get("date", ""))}</span><span class="tag">Shadow only</span><span class="tag">Paper only</span></div>
    <h1>JEV decision service</h1>
    <p class="purpose">A separate job beside Mirai station: it reads what SNDK PRO already stores, turns the numbers into {STATUS_COUNT['built']} sentences, has JEV answer {N_Q - n_dark} small questions in parallel ({n_dark} more are dark until a news source is plugged in), and writes one card the phone polls. Below: how it fits, how a label is built, the six-step pipeline with its files, and which question reads which label.</p>
    <nav class="jump" aria-label="Sections">
      <a class="chip" href="#fit">How it fits</a><a class="chip" href="#builder">The state builder</a><a class="chip" href="#pipeline">The pipeline</a><a class="chip" href="#wlog">Weights</a><a class="chip" href="#map">Label to question map</a>
    </nav>
  </header>

  <section class="sec" id="fit">
    <h2>How it fits together</h2>
    <figure class="fig"><div class="scroll">{fit_diagram()}</div>
    <figcaption>Mirai station is unchanged: the scan, the bar sidecar and the Reader keep writing their files. The JEV decision service is its own launchd job, 20 seconds behind each scan; it only reads those files and writes into state/jev/. The viewstation already serves any state file read-only, so the phone's decision card needs no new route. Dashed boxes come later; the red dotted box is an input Mirai does not have.</figcaption></figure>
  </section>

  <section class="sec" id="builder">
    <h2>The state builder</h2>
    <figure class="fig"><div class="scroll">{state_diagram()}</div>
    <figcaption>Every label follows the same path: pin the moment, take the raw numbers, put them on a scale, judge against a cut, write one sentence with both the number and the cut in it. A label that cannot be measured is left out rather than guessed, and the packer then skips any question that needed it.</figcaption></figure>
  </section>

  <section class="sec" id="pipeline">
    <h2>The pipeline, its own six steps</h2>
    <figure class="fig"><div class="scroll">{pipeline_diagram()}</div>
    <figcaption>One run per half hour, at :02 and :32. Steps 1 and 3 are code, steps 2 and 4 are JEV, step 5 is a file the phone polls. Step 6 runs every tick but can only grade a record whose 30 and 60 minutes have passed, and it moves nothing until 40 records are graded. The sums are forecasts: graded, never a call.</figcaption></figure>
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

  <p class="foot">Built from spec/labels.json and questions/sndk_pro.json in skills/sndk-jev. Every label sentence is from a real stored row (the 21 Sept close, or 18 Sept 12:45 where the newer book lacked it) except the news ones, which are example wording because no feed exists. Nothing has been sent to JEV yet: no key on this machine.</p>
</div>
"""
open(OUT, "w", encoding="utf-8").write(page)
skeleton = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '<style>:root{color-scheme:light;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif;background:#fafafa}img{max-width:100%}[hidden]{display:none!important}</style></head><body>')
open(OUT.replace(".html", "-preview.html"), "w", encoding="utf-8").write(skeleton + page + "</body></html>")
print(f"built {OUT} {len(page.encode('utf-8'))} bytes; {N_Q} questions in {N_G} groups; labels {dict(STATUS_COUNT)}; base rates n={BR['n'] if BR else 0}")
