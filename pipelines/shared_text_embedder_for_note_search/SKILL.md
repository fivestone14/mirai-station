---
name: mirai-right-eye
description: |
  Internal text-embedding helper for mirai-station (NOT user-invokable).
  Exposes bge-small-en-v1.5 sentence embeddings (384-d) so other components can
  turn text into a vector. Two live consumers, both memories' semantic rankers:
  lefteye_rag (the Watchtower's memory) and sndk_rag (the SNDK reading's), each
  of which imports embed.py straight off this directory and reads config.json
  itself to rank its stored slices against a query. (The morning macro brief,
  which embedded its read through runtime/watch/right_eye_skill, retired
  2026-10-11; the loader and _config.py went with it.)

  RETIRED 2026-07-10: the former RAG news-memory pipeline (per-item embed →
  novelty gate → classify → score → summarize → LanceDB store, plus retrieve /
  feedback / self_review) was orphaned when the Mirai Watch tick graph was
  removed ~2026-06-11 and never re-wired. Those modules and the frozen
  state/right_eye.lance store were deleted. Only embed.py remains live. Do
  not invoke this as a slash command.
argument-hint: ""
allowed-tools: Read
---

> **Interpreter**: a Python module imported directly by lefteye_rag and sndk_rag.
> The single live entry point is `embed.embed_text(text, config) -> list[float]`
> (a 384-d bge-small vector); each caller reads the `embedding_model` block from
> `config.json` itself. Python pins
> `~/.local/share/mirai-station/venv/bin/python`.

# mirai-right-eye — Embedding Helper

What survives is the embedding surface only:

- `embed.embed_text(text, config)` → 384-dimension `bge-small-en-v1.5` vector
  (the model is cached under `~/.cache/huggingface`; `sentence_transformers` is
  imported lazily on first use).
- `config.json`, which holds only the `embedding_model` block (the store path,
  audit log dir and the news-scoring keys went with the retired pipeline;
  `_config.py`, its loader, went with the macro brief on 2026-10-11, since both
  callers read the file themselves). (`schemas.py` and its pydantic types went
  the same way — deleted 2026-07-20.)

## Live wiring

`skills/mirai-left-eye/lefteye_rag.py` and `skills/sndk-pro/sndk_rag.py` each
put this directory on `sys.path`, import `embed_text` and read `config.json`
themselves to rank their memory slices semantically; if the model cannot load
they fall back to their lexical ranker and say so in the output.

Nothing reads a persisted store; there is no news memory anymore.

## History

The original design was a stateless per-item news/RAG intelligence engine
(novelty gate + classify + score + summarize + LanceDB retrieve + a feedback /
self-review learning loop). That pipeline lost its invoker when the tick graph
was retired and sat dead until 2026-07-10, when the orphaned code and its frozen
Lance store were removed.
