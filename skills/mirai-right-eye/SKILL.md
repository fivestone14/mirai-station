---
name: mirai-right-eye
description: |
  Internal text-embedding helper for mirai-station (NOT user-invokable).
  Exposes bge-small-en-v1.5 sentence embeddings (384-d) so other components can
  turn text into a vector. Two live consumers: macro_mood, which loads it via
  runtime/watch/right_eye_skill.load("embed", "embed_text") to embed the morning
  macro expectation for the day's drift_cosine (how far the tape has moved from
  the pre-open read); and lefteye_rag, the Watchtower's memory, which imports
  embed.py straight off this directory to rank its stored slices against a query.

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
> `runtime/watch/right_eye_skill.py`, or imported directly by lefteye_rag. The
> single live entry point is `embed.embed_text(text, config) -> list[float]` (a
> 384-d bge-small vector), with `_config.load()` supplying the `embedding_model`
> block from `config.json`. Python pins
> `~/.local/share/mirai-station/venv/bin/python`.

# mirai-right-eye — Embedding Helper

What survives is the embedding surface only:

- `embed.embed_text(text, config)` → 384-dimension `bge-small-en-v1.5` vector
  (the model is cached under `~/.cache/huggingface`; `sentence_transformers` is
  imported lazily on first use).
- `_config.load()` → `config.json`, which now holds only the `embedding_model`
  block (the store path, audit log dir and the news-scoring keys went with the
  retired pipeline). (`schemas.py` and its pydantic types went the same way —
  deleted 2026-07-20.)

## Live wiring

`runtime/watch/intraday/macro_mood.py` (`make_right_eye_embedder`) calls
`right_eye_skill.load("embed", "embed_text")` and `load("_config", "load")`
during the daily macro brief (and the intraday wall-breach re-dive). The vector
it returns feeds `drift_cosine`.

`skills/mirai-left-eye/lefteye_rag.py` puts this directory on `sys.path`,
imports `embed_text` and reads `config.json` itself to rank the Watchtower's
memory slices semantically; if the model cannot load it falls back to its
lexical ranker and says so in the output.

Nothing reads a persisted store; there is no news memory anymore.

## History

The original design was a stateless per-item news/RAG intelligence engine
(novelty gate + classify + score + summarize + LanceDB retrieve + a feedback /
self-review learning loop). That pipeline lost its invoker when the tick graph
was retired and sat dead until 2026-07-10, when the orphaned code and its frozen
Lance store were removed.
