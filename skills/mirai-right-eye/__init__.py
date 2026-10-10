"""mirai-right-eye — text embedding surface.

The RAG news-memory pipeline (process_item / retrieve / store / classify /
novelty / score / summarize / feedback / self_review) was retired 2026-07-10;
only the embedding half remains live.

Live entry points (loaded via runtime/watch/right_eye_skill.load):
    embed.embed_text(text, config) -> list[float]   # 384-d bge-small vector
    _config.load() -> dict                          # the embedding_model block
Consumed by macro_mood (the morning expectation's vector, for drift_cosine) and
by lefteye_rag's semantic ranker, which imports embed.py off this directory and
reads config.json itself.
"""
