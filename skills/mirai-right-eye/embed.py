"""Stage 1 — embedding.

Default: local sentence-transformers (bge-small-en-v1.5, 384-d). Swappable
via config. Single global model cached per process.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .schemas import Item


@lru_cache(maxsize=1)
def _model(name: str):
    # Lazy import — sentence-transformers is heavy.
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(name)


def embed_text(text: str, config: dict[str, Any]) -> list[float]:
    """Embed a single string. Returns a list[float] of length dimension."""
    em = config["embedding_model"]
    provider = em["provider"]

    if provider == "sentence-transformers":
        model = _model(em["name"])
        vec = model.encode(text, normalize_embeddings=True)
        return vec.astype("float32").tolist()

    raise NotImplementedError(f"embedding provider not wired: {provider}")


def embed_item(item: Item, config: dict[str, Any]) -> list[float]:
    """Embed an item. Skips if item already carries embedding (host batched)."""
    if item.embedding is not None:
        return list(item.embedding)
    payload_text = f"{item.title}\n\n{item.body}".strip()
    return embed_text(payload_text, config)


def embed_query(query: str, config: dict[str, Any]) -> list[float]:
    """Embed a free-text query for similarity search (delegates to embed_text)."""
    return embed_text(query, config)
