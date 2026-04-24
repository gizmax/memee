"""Embedding engine: local vector embeddings via sentence-transformers.

Uses all-MiniLM-L6-v2 (22M params, 384 dims) — fast, local, no API keys.
Falls back gracefully if sentence-transformers is not installed.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()
_model_load_failed = False


def get_model():
    """Lazy-load the embedding model (downloads ~80MB on first use).

    Thread-safe: two concurrent callers won't both kick off a download,
    which previously caused races on the HuggingFace cache directory.
    Double-checked locking keeps the fast-path lock-free after the first
    successful load.
    """
    global _model, _model_load_failed
    if _model is not None:
        return _model
    if _model_load_failed:
        # Don't re-attempt forever on a broken environment.
        return None
    with _model_lock:
        if _model is not None:  # Another thread loaded it while we waited.
            return _model
        if _model_load_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Loaded embedding model: all-MiniLM-L6-v2")
        except Exception as e:
            logger.warning(
                f"Failed to load embedding model: {e}. "
                f"Vector search disabled. Install: pip install memee[vectors]"
            )
            _model_load_failed = True
            return None
    return _model


def embed_text(text: str) -> list[float]:
    """Embed a single text string. Returns 384-dim float vector or empty list."""
    model = get_model()
    if model is None:
        return []
    try:
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return []


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in batch. Returns empty lists if model unavailable."""
    if not texts:
        return []
    model = get_model()
    if model is None:
        return [[] for _ in texts]
    try:
        embeddings = model.encode(texts, normalize_embeddings=True, batch_size=64)
        return [e.tolist() for e in embeddings]
    except Exception as e:
        logger.warning(f"Batch embedding failed: {e}")
        return [[] for _ in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two normalized vectors.

    With normalized vectors, cosine similarity = dot product.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def embed_memory_text(title: str, content: str, tags: list[str] | None = None) -> list[float]:
    """Create an embedding from memory fields, optimized for search."""
    parts = [title]
    if content and content != title:
        parts.append(content[:500])
    if tags:
        parts.append("Tags: " + ", ".join(tags))
    return embed_text(" ".join(parts))
