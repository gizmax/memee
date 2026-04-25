"""Learning-to-Rank (LTR) for Memee retrieval (R9 #3).

This module owns the ranker lifecycle:
  * Feature extraction at search time (build a vector per candidate).
  * Loading the active production model from the DB registry.
  * Optional canary routing for A/B comparison.
  * Training (offline) from the ``search_ranking_snapshots`` mined pairs.

Optional dependency: ``lightgbm``. When unavailable, every public function
that would invoke a model returns ``None`` / no-op so the rest of the
search pipeline transparently falls back to the heuristic stack
(RRF + tag/confidence boost + title/intent multipliers). This keeps the
OSS install footprint slim — LTR is opt-in via ``pip install memee[ltr]``.

Feature schema (v1, 11 features) — kept in sync with
``tests/retrieval_eval.py`` so both the trainer and the eval harness see
the same vector layout:

    bm25_normalized_score  float ∈ [0, 1]
    bm25_rank              int   (-1 sentinel when missing)
    vector_cosine          float ∈ [0, 1]
    vector_rank            int   (-1 sentinel when missing)
    rrf_score              float
    confidence_score       float ∈ [0, 1]
    maturity_multiplier    float (canon=1.0, validated=0.85, …)
    validation_count       int
    type_encoded           int   (pattern=0, decision=1, anti_pattern=2,
                                  lesson=3, observation=4, other=-1)
    query_length_chars     int
    has_question_mark      0 / 1
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Maturity multipliers MUST match search.MATURITY_MULTIPLIER exactly so the
# feature value at training time and inference time are identical. We avoid
# importing search.py here to keep ltr.py free of cycles.
_MATURITY_MULT = {
    "canon": 1.0,
    "validated": 0.85,
    "tested": 0.65,
    "hypothesis": 0.4,
    "deprecated": 0.05,
}

_TYPE_ENCODE = {
    "pattern": 0,
    "decision": 1,
    "anti_pattern": 2,
    "lesson": 3,
    "observation": 4,
}

FEATURE_NAMES = (
    "bm25_normalized_score",
    "bm25_rank",
    "vector_cosine",
    "vector_rank",
    "rrf_score",
    "confidence_score",
    "maturity_multiplier",
    "validation_count",
    "type_encoded",
    "query_length_chars",
    "has_question_mark",
)


def featurize(
    *,
    query: str,
    memory,
    bm25_score: float,
    bm25_rank: int | None,
    vector_score: float,
    vector_rank: int | None,
    rrf_score: float,
) -> list[float]:
    """Build the v1 feature vector for one (query, candidate) pair.

    All fields default to safe sentinels so partial information (no vector
    rank, missing maturity) cannot crash inference. Order MUST match
    ``FEATURE_NAMES``.
    """
    q_text = query or ""
    return [
        float(bm25_score or 0.0),
        float(-1 if bm25_rank is None else bm25_rank),
        float(vector_score or 0.0),
        float(-1 if vector_rank is None else vector_rank),
        float(rrf_score or 0.0),
        float(getattr(memory, "confidence_score", 0.0) or 0.0),
        _MATURITY_MULT.get(getattr(memory, "maturity", "") or "", 0.5),
        float(getattr(memory, "validation_count", 0) or 0),
        float(_TYPE_ENCODE.get(getattr(memory, "type", "") or "", -1)),
        float(len(q_text)),
        1.0 if "?" in q_text else 0.0,
    ]


# ── Active-model registry ─────────────────────────────────────────────────

# Module-level cache of the active production model. Populated on first
# call to ``load_active_model`` and refreshed when the active row changes.
# Tested under threading.Lock so a concurrent canary lookup doesn't trigger
# duplicate file loads.

_MODEL_LOCK = threading.Lock()
_CACHED: dict[str, Any] = {"id": None, "version": None, "predict": None}


def _import_lightgbm():
    try:
        import lightgbm as lgb  # type: ignore

        return lgb
    except ImportError:
        return None


def is_enabled() -> bool:
    """LTR is on iff ``MEMEE_LTR_ENABLED`` is ``1`` or ``canary`` AND the
    optional ``lightgbm`` dep is importable. ``0`` (default) skips loading.
    """
    flag = os.environ.get("MEMEE_LTR_ENABLED", "0").strip().lower()
    return flag in ("1", "canary") and _import_lightgbm() is not None


def routing_mode() -> str:
    """Return ``off`` | ``on`` | ``canary``. Used by callers that need to
    decide per-request whether to route through LTR."""
    flag = os.environ.get("MEMEE_LTR_ENABLED", "0").strip().lower()
    if flag in ("1", "true"):
        return "on"
    if flag == "canary":
        return "canary"
    return "off"


def canary_threshold() -> float:
    """Fraction of traffic to route through the LTR ranker in canary mode.
    Defaults to 0.10 (10 %); override via ``MEMEE_LTR_CANARY_FRACTION``.
    """
    raw = os.environ.get("MEMEE_LTR_CANARY_FRACTION", "0.1")
    try:
        v = float(raw)
        return min(max(v, 0.0), 1.0)
    except ValueError:
        return 0.1


def canary_picks_ltr(seed: str) -> bool:
    """Stable per-key canary routing — same query bucket always goes the
    same way. Uses a cheap hash so we don't need ``hashlib`` overhead."""
    if not seed:
        return False
    bucket = (sum(ord(c) for c in seed) % 1000) / 1000.0
    return bucket < canary_threshold()


def load_active_model(session: Session) -> dict | None:
    """Look up the current production model and return ``{id, version,
    predict}`` or ``None`` if no usable model exists. Cached across calls."""
    if not is_enabled():
        return None
    from memee.storage.models import LTRModel

    row = (
        session.query(LTRModel)
        .filter(LTRModel.status == "production")
        .order_by(desc(LTRModel.activated_at))
        .first()
    )
    if row is None:
        return None
    with _MODEL_LOCK:
        if _CACHED["id"] == row.id and _CACHED["predict"] is not None:
            return dict(_CACHED)
        lgb = _import_lightgbm()
        if lgb is None:
            return None
        try:
            booster = lgb.Booster(model_file=str(row.path))
        except Exception as e:
            logger.warning("LTR model load failed (%s); falling back: %s", row.path, e)
            return None
        _CACHED["id"] = row.id
        _CACHED["version"] = row.version
        _CACHED["predict"] = booster.predict
        return dict(_CACHED)


# ── Training ───────────────────────────────────────────────────────────────


def export_training_pairs(session: Session) -> list[dict]:
    """Return a list of (query, candidate_features, label, group) rows for
    pairwise training. Each ``SearchEvent`` with ``accepted_memory_id`` set
    becomes one group; rows above the accepted position get label=1, the
    accepted row gets label=2, rows below get label=0.

    Memory state at search time is read from ``SearchRankingSnapshot`` so
    edits to the underlying ``Memory`` row don't poison features.
    """
    from memee.storage.models import SearchEvent, SearchRankingSnapshot

    accepted_events = (
        session.query(SearchEvent)
        .filter(SearchEvent.accepted_memory_id.isnot(None))
        .all()
    )
    out: list[dict] = []
    for ev in accepted_events:
        snaps = (
            session.query(SearchRankingSnapshot)
            .filter(SearchRankingSnapshot.event_id == ev.id)
            .order_by(SearchRankingSnapshot.rank.asc())
            .all()
        )
        if not snaps:
            continue
        accepted_idx = next(
            (i for i, s in enumerate(snaps) if s.memory_id == ev.accepted_memory_id),
            None,
        )
        if accepted_idx is None:
            continue
        for i, snap in enumerate(snaps):
            label = 2 if i == accepted_idx else (1 if i < accepted_idx else 0)
            features = [
                snap.bm25_score or 0.0,
                snap.bm25_rank if snap.bm25_rank is not None else -1,
                snap.vector_score or 0.0,
                snap.vector_rank if snap.vector_rank is not None else -1,
                snap.rrf_score or 0.0,
                snap.memory_confidence or 0.0,
                _MATURITY_MULT.get(snap.memory_maturity or "", 0.5),
                snap.memory_validation_count or 0,
                _TYPE_ENCODE.get(snap.memory_type or "", -1),
                len(ev.query_text or ""),
                1 if "?" in (ev.query_text or "") else 0,
            ]
            out.append(
                {
                    "event_id": ev.id,
                    "memory_id": snap.memory_id,
                    "features": features,
                    "label": label,
                }
            )
    return out


def train_and_register(
    session: Session,
    output_dir: Path,
    *,
    version: str,
    eval_metrics: dict | None = None,
) -> str | None:
    """Train a LightGBM ranker on the accepted-pair dataset and register it
    as a ``candidate``. Returns the model id, or ``None`` if there isn't
    enough data / lightgbm is missing.

    Activation is a separate step (`memee research promote-ranker <id>`)
    so the trainer can run nightly without auto-flipping prod.
    """
    lgb = _import_lightgbm()
    if lgb is None:
        logger.info("lightgbm not installed; skipping LTR training")
        return None
    rows = export_training_pairs(session)
    if len(rows) < 30:
        logger.info("only %d training rows; need at least 30", len(rows))
        return None

    import numpy as np

    X = np.asarray([r["features"] for r in rows], dtype=np.float32)
    y = np.asarray([r["label"] for r in rows], dtype=np.int32)
    # Group sizes for LightGBM lambdarank: number of rows per query.
    groups: list[int] = []
    last_event = None
    for r in rows:
        if r["event_id"] != last_event:
            groups.append(0)
            last_event = r["event_id"]
        groups[-1] += 1

    train_data = lgb.Dataset(X, label=y, group=groups)
    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [3, 5, 10],
        "learning_rate": 0.08,
        "num_leaves": 31,
        "verbosity": -1,
    }
    booster = lgb.train(params, train_data, num_boost_round=200)

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"ranker_{version}.txt"
    booster.save_model(str(path))

    from memee.storage.models import LTRModel

    record = LTRModel(
        version=version,
        path=str(path),
        status="candidate",
        eval_ndcg_at_10=(eval_metrics or {}).get("ndcg10"),
        eval_recall_at_5=(eval_metrics or {}).get("recall5"),
        eval_mrr=(eval_metrics or {}).get("mrr"),
        training_event_count=len({r["event_id"] for r in rows}),
        notes=json.dumps({"feature_names": FEATURE_NAMES}),
    )
    session.add(record)
    session.commit()
    logger.info("LTR model %s registered (id=%s)", version, record.id)
    return record.id


def promote(session: Session, model_id: str) -> bool:
    """Flip a candidate to production; demote the previous prod to deprecated."""
    from memee.storage.models import LTRModel

    target = session.get(LTRModel, model_id)
    if target is None:
        return False
    prev = (
        session.query(LTRModel)
        .filter(LTRModel.status == "production", LTRModel.id != model_id)
        .all()
    )
    for p in prev:
        p.status = "deprecated"
    target.status = "production"
    target.activated_at = utcnow()
    session.commit()
    # Force the next load to refresh.
    with _MODEL_LOCK:
        _CACHED["id"] = None
        _CACHED["predict"] = None
    return True


# Local helper to dodge the circular import.
def utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
