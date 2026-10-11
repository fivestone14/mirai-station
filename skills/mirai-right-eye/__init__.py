"""mirai-right-eye — text embedding surface.

The RAG news-memory pipeline (process_item / retrieve / store / classify /
novelty / score / summarize / feedback / self_review) was retired 2026-07-10;
only the embedding half remains live.

Live entry point:
    embed.embed_text(text, config) -> list[float]   # 384-d bge-small vector
Consumed by the semantic rankers of lefteye_rag and sndk_rag, which import
embed.py off this directory and read config.json themselves. (The morning macro
brief, which loaded it through runtime/watch/right_eye_skill, retired 2026-10-11.)
"""
