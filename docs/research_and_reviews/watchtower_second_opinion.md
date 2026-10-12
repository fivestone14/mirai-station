---
cssclasses:
  - mirai
---

# The Watchtower — Head B, the reasoning judge

> A dated note: the paths and folder names in it are those of its day. Where every store lives today, with its writer and readers, is `docs/data_map.md` at the repo root.

This page used to describe the build of 2026-07-05: shadow only, blind first,
three votes with the majority winning, fail-open, graded on the gates' own
rules. That build is long gone. The Watchtower now runs on prompt era wt-11,
keeps its own memory of past reads and may search the web for the catalyst
behind an abnormal tape; whether it stays at all is an open decision in the
cleanup review of 2026-10-10.

Where the live code and its current description are:

| What | Where |
|:--|:--|
| The head itself | `watchtower.py` (shortcut in this folder) → `~/.claude/plugins/mirai-station/skills/mirai-left-eye/watchtower.py` |
| Its memory | `skills/mirai-left-eye/lefteye_rag.py` over `state/spx_rag/` |
| The rules it runs on | the plugin `README.md`, section 1 "The three heads"; `docs/spx-payload-inventory.md`; `docs/wt8-doctrine.md` |
| Its verdicts | the diary, `state/reversion/{date}.jsonl`, key `watchtower` |
| Its report card | `state/reversion/polarity-{day}.json`, key `watchtower` |
| Offline replay | `python3 watchtower.py --replay YYYY-MM-DD [--live]` |

It still has no path to the phone and cannot place, size or veto a trade.
