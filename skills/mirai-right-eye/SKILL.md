---
name: mirai-right-eye
description: |
  Internal text-embedding helper for mirai-station (NOT user-invokable).
  Exposes bge-small-en-v1.5 sentence embeddings (384-d) so other components can
  turn text into a vector. Loaded programmatically via
  runtime/watch/right_eye_skill.load("embed", "embed_text"); its only live
  consumer is macro_mood, which embeds the morning macro expectation to compute
  the day's drift_cosine (how far the tape has moved from the pre-open read).

  RETIRED 2026-07-10: the former RAG news-memory pipeline (per-item embed →
  novelty gate → classify → score → summarize → LanceDB store, plus retrieve /
  feedback / self_review) was orphaned when the Mirai Watch tick graph was
  removed ~2026-06-11 and never re-wired. Those modules and the frozen
  state/right_eye.lance store were deleted. Only embed.py + _config.py
  remain live. Do not invoke this as a slash command.
argument-hint: ""
allowed-tools: Read
---

> **Interpreter**: a Python module loaded on demand via
> `runtime/watch/right_eye_skill.py`. The single live entry point is
> `embed.embed_text(text) -> list[float]` (a 384-d bge-small vector), with
> `_config.load()` supplying the model id + config from `config.json`. Python
> pins `~/.local/share/mirai-station/venv/bin/python`.

# mirai-right-eye — Embedding Helper

What survives is the embedding surface only:

- `embed.embed_text(text)` → 384-dimension `bge-small-en-v1.5` vector (the
  model is cached under `~/.cache/huggingface`; `sentence_transformers` is
  imported lazily on first use).
- `_config.load()` → the `embedding_model` block from `config.json`.
  (`schemas.py` and its pydantic types went with the retired pipeline —
  deleted 2026-07-20.)

## Live wiring

`runtime/watch/intraday/macro_mood.py` (`make_right_eye_embedder`) calls
`right_eye_skill.load("embed", "embed_text")` and `load("_config", "load")`
during the daily macro brief (and the intraday wall-breach re-dive). The vector
it returns feeds `drift_cosine` — the only place the right eye touches the
system today. Nothing reads a persisted store; there is no news memory anymore.

## History

The original design was a stateless per-item news/RAG intelligence engine
(novelty gate + classify + score + summarize + LanceDB retrieve + a feedback /
self-review learning loop). That pipeline lost its invoker when the tick graph
was retired and sat dead until 2026-07-10, when the orphaned code and its frozen
Lance store were removed. A snapshot is preserved at
`~/.claude/plugins/mirai-station.backup-right-eye-rag-20260710.tgz` if the
memory pipeline is ever revived.
