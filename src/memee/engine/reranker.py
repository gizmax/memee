"""Cross-encoder reranker (R14).

A cross-encoder takes a (query, document) pair and produces a single
relevance score by attending across both texts at once. Unlike the bi-encoder
embeddings used in ``embeddings.py`` (which encodes the query and the
document independently and compares via cosine), a cross-encoder can resolve
paraphrases, synonyms and negations the bi-encoder misses — exactly the
pathologies the R12 retrieval eval flagged on the ``paraphrastic`` (n=43,
nDCG@10 = 0.6795) and ``lexical_gap_hard`` (n=15, 0.7446) clusters.

The trade-off is latency. A cross-encoder must run one forward pass per
candidate, so we never run it on the whole corpus. We keep it in stage 5 of
``search_memories`` and only score the top-K of the RRF candidate list (K=30
by default), which keeps the budget at ~50–200 ms per query on CPU. For
that reason the reranker is **default OFF**; opt-in via the
``MEMEE_RERANK_MODEL`` env var.

Optional dependency: ``sentence-transformers``. The same package backs
``embeddings.py``, so installing ``memee[vectors]`` already gives you the
cross-encoder support too. We expose an explicit ``memee[rerank]`` extra for
clarity in installs that don't want bi-encoder vectors.

Usage from the search pipeline:

    rr = CrossEncoderReranker()
    if rr.is_enabled():
        results = rr.rerank(query, results, top_k=30)

Failure mode: if the model can't be loaded (no internet + no cache, or
``sentence-transformers`` not installed) the reranker disables itself for
the rest of the process and returns the input list unchanged. The
heuristic + LTR stack continues to operate; you simply lose the lift on
paraphrastic queries until the cache is present.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


# Module-level cache. The cross-encoder weights are 80–500 MB depending on
# the model — loading them per-query would dwarf the rerank latency. We pin
# one instance per process behind a lock and reuse it for every search.
_MODEL_LOCK = threading.Lock()
_CACHED_MODEL: Any = None
_CACHED_MODEL_NAME: str | None = None
_LOAD_FAILED = False


# Default model. ms-marco-MiniLM-L-6-v2 is the canonical lightweight cross-
# encoder: 22M params, 80 MB, ~5 ms / pair on CPU. Trained on MS MARCO so it
# generalises well across English search tasks. Override via the env var.
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Top-K rerank window. Smaller is cheaper (linear in K). 30 mirrors the
# RRF candidate pool size — going higher pays for candidates that the
# upstream retriever already deemed irrelevant.
DEFAULT_RERANK_TOP_K = 30


def _model_name_from_env() -> str | None:
    """Return the configured model name, or ``None`` if rerank is off.

    Recognised env values:
      - unset / empty: rerank OFF (default)
      - ``ms-marco-MiniLM-L-6-v2``: shorthand → ``cross-encoder/ms-marco-MiniLM-L-6-v2``
      - any path containing ``/``: passed through as-is (HF hub id or local path)
    """
    raw = os.environ.get("MEMEE_RERANK_MODEL", "").strip()
    if not raw:
        return None
    if "/" in raw:
        return raw
    # Common shorthand: ms-marco-* → cross-encoder/ms-marco-*
    return f"cross-encoder/{raw}"


def _top_k_from_env() -> int:
    raw = os.environ.get("MEMEE_RERANK_TOP_K", "")
    try:
        v = int(raw)
        return max(1, v)
    except (TypeError, ValueError):
        return DEFAULT_RERANK_TOP_K


def is_enabled() -> bool:
    """True iff a model name is configured AND ``sentence-transformers`` is
    importable AND the model hasn't already failed to load this process.

    We deliberately do not pre-import ``sentence_transformers`` here; that
    import is ~5 s cold and would bite every search even on installs that
    never set ``MEMEE_RERANK_MODEL``. The check is cheap (env var only) until
    a real rerank request comes in.
    """
    if _LOAD_FAILED:
        return False
    return _model_name_from_env() is not None


def _try_load(model_name: str) -> Any:
    """Load and cache the cross-encoder, honouring sentence-transformers
    offline mode (``HF_HUB_OFFLINE=1`` / ``TRANSFORMERS_OFFLINE=1``). On any
    failure we set ``_LOAD_FAILED`` so subsequent calls short-circuit.
    """
    global _CACHED_MODEL, _CACHED_MODEL_NAME, _LOAD_FAILED
    if _CACHED_MODEL is not None and _CACHED_MODEL_NAME == model_name:
        return _CACHED_MODEL
    with _MODEL_LOCK:
        if _CACHED_MODEL is not None and _CACHED_MODEL_NAME == model_name:
            return _CACHED_MODEL
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            logger.info(
                "sentence-transformers not installed; rerank disabled. "
                "Install: pip install memee[rerank]"
            )
            _LOAD_FAILED = True
            return None
        try:
            model = CrossEncoder(model_name)
        except Exception as e:
            logger.warning(
                "Cross-encoder load failed (%s); rerank disabled this run: %s",
                model_name,
                e,
            )
            _LOAD_FAILED = True
            return None
        _CACHED_MODEL = model
        _CACHED_MODEL_NAME = model_name
        logger.info("Loaded cross-encoder: %s", model_name)
        return model


def _candidate_text(memory) -> str:
    """Build the document side of a (query, document) pair.

    ``title + content[:200]`` is the same shape the bi-encoder's
    ``embed_memory_text`` uses for the title+content slice, so the two
    rankers see comparable context. We don't include tags here — the cross-
    encoder is good at semantic matching and tag noise tends to dilute the
    score; the heuristic stack already weights tag overlap as a separate
    signal.
    """
    title = getattr(memory, "title", "") or ""
    content = getattr(memory, "content", "") or ""
    if not content:
        return title
    if not title:
        return content[:200]
    return f"{title} {content[:200]}"


class CrossEncoderReranker:
    """Stage-5 reranker over the RRF candidate stack.

    The ranker is a thin wrapper around a cross-encoder model. ``rerank``
    takes the heuristic-ranked candidate list and reorders the top-K window
    by cross-encoder score; everything below K keeps its RRF order so we
    don't pay latency on the long tail.

    Stateless from the caller's point of view: re-instantiation is cheap
    because the model is module-cached. The class only holds the env-derived
    ``model_name`` and ``top_k`` so callers can introspect / override.
    """

    def __init__(
        self,
        model_name: str | None = None,
        top_k: int | None = None,
    ):
        self.model_name = model_name or _model_name_from_env()
        self.top_k = top_k or _top_k_from_env()

    def is_enabled(self) -> bool:
        if _LOAD_FAILED:
            return False
        return self.model_name is not None

    def cache_state(self) -> dict:
        """Diagnostic: tell the CLI what the rerank stack is doing.

        Loaded ``True`` means the cross-encoder is held in memory and the
        next ``rerank`` call will skip the cold-start cost. ``False``
        usually means rerank hasn't fired yet this process — not an error.
        """
        return {
            "model_name": self.model_name,
            "top_k": self.top_k,
            "loaded": _CACHED_MODEL is not None,
            "cached_model_name": _CACHED_MODEL_NAME,
            "load_failed": _LOAD_FAILED,
        }

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int | None = None,
    ) -> list[dict]:
        """Rerank ``candidates`` (heuristic-ranked dicts from search_memories).

        Each candidate must carry a ``memory`` key with at least a ``title``
        and ``content`` attribute. The returned list has the same length and
        contains the same dicts (with an added ``cross_encoder_score`` and
        re-rounded ``total_score``); only the order changes.

        Reorder rule:
          * Score the top ``K`` candidates with the cross-encoder.
          * Sort *those* by cross-encoder score, descending.
          * Keep candidates beyond ``K`` in their existing order, appended
            after the reranked window.

        This preserves recall on the long tail (where the cross-encoder
        wouldn't have run anyway) and uses the cross-encoder strictly as a
        precision lever on the top of the list.
        """
        if not self.is_enabled() or not candidates:
            return candidates

        k = top_k or self.top_k
        if k <= 0:
            return candidates

        head = candidates[:k]
        tail = candidates[k:]

        model = _try_load(self.model_name)
        if model is None:
            return candidates

        pairs = [(query, _candidate_text(c["memory"])) for c in head]
        try:
            # ``predict`` returns a numpy array; cast to floats for the
            # candidate dict so the JSON-serialising telemetry layer doesn't
            # choke on numpy types.
            raw_scores = model.predict(pairs)
        except Exception as e:
            logger.warning("Cross-encoder predict failed: %s", e)
            return candidates

        # The cross-encoder score is unbounded (can be negative on bad
        # matches). We don't try to normalise to [0,1] — downstream we sort
        # and ignore magnitudes.
        for cand, score in zip(head, raw_scores):
            cand["cross_encoder_score"] = float(score)
            # Stamp the rerank into total_score so downstream consumers
            # (telemetry, post-task feedback) see the post-rerank ranking
            # as the canonical one. The original heuristic total stays in
            # ``features`` for debugging.
            feats = cand.setdefault("features", {})
            feats["pre_rerank_total"] = cand.get("total_score")

        head.sort(key=lambda c: c["cross_encoder_score"], reverse=True)
        return head + tail


def reset_for_tests() -> None:
    """Drop the cached model. Used by tests that toggle the flag mid-run."""
    global _CACHED_MODEL, _CACHED_MODEL_NAME, _LOAD_FAILED
    with _MODEL_LOCK:
        _CACHED_MODEL = None
        _CACHED_MODEL_NAME = None
        _LOAD_FAILED = False
