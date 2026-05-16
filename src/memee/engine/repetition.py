"""Active re-validation of canon memories (v2.4.1 — Tier 1.6).

The cognitive-science dossier flagged the single biggest honesty
problem with Memee's lifecycle: **canon is currently unfalsifiable**.
A memory that hit canon two years ago and is silently stale only ever
deprecates if somebody invalidates it. Survivorship bias — confidence
keeps drifting upward, never tested against the world.

Spaced-repetition literature (Ebbinghaus → SuperMemo SM-2 → FSRS,
Anki 23.10+ default) solved this for human memory. The fix is to
*actively schedule* re-tests of items where the stability of recall
is uncertain, and let the test outcome update the underlying model.

For Memee, the Beta-Binomial posterior (v2.4.0) gives us the right
"is this memory uncertain?" signal for free — wide HDI on a canon
claim means "we say it's solid but we have surprisingly little
evidence for that." Stale last-applied-at adds the
"haven't been re-tested in a while" dimension. We rank canon by a
combined score and surface the top N in the next briefing's
**Layer 0.7** ("Re-checking canon:") block.

Feedback closes via the existing `update_confidence` pipeline:

  * Agent applies a verify candidate (via `MemoryUsage` or
    `SearchEvent.accepted_memory_id`) → already wired to α += w.
  * Agent records a contradicting / superseding memory → existing
    contradicts/supersedes pipeline + β += w from any explicit
    invalidate.
  * Skipped repeatedly → the next ``select_verify_candidates`` call
    will pick the same memory again because nothing changed —
    eventually the operator notices it and acts.

Kill switches: ``MEMEE_QUIET=1`` (master) and ``MEMEE_NO_VERIFY=1``
(per-channel). Symmetric with Layer 0 / 0.5 conventions.

References:
  * Wozniak SM-2 (1990): https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler
  * FSRS / Ye et al. KDD 2022 (deployed as Anki default since 23.10).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from memee.engine.confidence import (
    get_uncertainty,
    predicted_retrievability,
)
from memee.storage.models import MaturityLevel, Memory


VERIFY_MIN_AGE_DAYS = 30
VERIFY_DEFAULT_LIMIT = 2
# Posterior SD threshold: above this, the memory's confidence is
# uncertain *relative to* its canon-tier claim and we surface it for
# re-test. Beta(11, 1) has SD ≈ 0.077 — anything wider than that on a
# canon row means we have surprisingly thin evidence for the claim.
VERIFY_SD_THRESHOLD = 0.07

# v2.4.5 (Tier 1.5): FSRS-light retrievability threshold. A canon
# memory with predicted recall ``R < 0.85`` is "about to slip" by
# SR-literature standards — Anki/FSRS default. Below this, surface
# for re-validation.
VERIFY_R_THRESHOLD = 0.85


def _layer07_suppressed() -> bool:
    """True iff the verify-candidate block (Layer 0.7) must be silent.

    Symmetric with ``_layer0_suppressed`` / ``_layer05_suppressed`` in
    ``router.py``:

      * ``MEMEE_QUIET=1``    — master cross-context shield.
      * ``MEMEE_NO_VERIFY=1`` — per-channel switch.

    Any non-empty value of either env var counts as set. Matches the
    convention used by other kill switches.
    """
    return bool(
        os.environ.get("MEMEE_QUIET") or os.environ.get("MEMEE_NO_VERIFY")
    )


def _verify_score(memory: Memory, now: datetime) -> float:
    """Compute a "re-test priority" score for a canon memory.

    Higher = more in need of re-validation. Two components:

      * **Retrievability gap (v2.4.5)** — per-memory FSRS recall
        prediction. ``R(t) = 2^(-Δt/h)`` where ``h`` is this memory's
        half-life. Memories below ``VERIFY_R_THRESHOLD = 0.85``
        contribute ``(threshold - R) × 20`` to the score — at the
        threshold = 0, at R=0 the gap saturates at 17. Replaces the
        pre-v2.4.5 global 30-day staleness cliff.
      * **Wide-HDI penalty** — posterior SD above the threshold means
        the canon claim has thin evidence. Each 0.01 of SD above
        threshold adds 1.0 to the score.

    Returns 0.0 when both signals are below their thresholds — those
    don't need re-checking yet.
    """
    R = predicted_retrievability(memory, now=now)
    retrievability_gap = max(0.0, VERIFY_R_THRESHOLD - R)

    sd = get_uncertainty(memory)
    sd_excess = max(0.0, sd - VERIFY_SD_THRESHOLD)

    if retrievability_gap <= 0 and sd_excess <= 0:
        return 0.0

    # Retrievability gap dominates: 0.85 → 0, 0.50 → 7.0,
    # 0.10 → 15.0. Caps at ~17 for fully-forgotten memories.
    fsrs_penalty = retrievability_gap * 20.0
    hdi_penalty = sd_excess * 100.0  # 0.01 SD → 1.0 score
    return fsrs_penalty + hdi_penalty


def select_verify_candidates(
    session: Session,
    *,
    limit: int = VERIFY_DEFAULT_LIMIT,
    now: datetime | None = None,
) -> list[Memory]:
    """Pick the top-N canon memories most in need of re-validation.

    Selection criteria:
      * ``maturity == CANON`` (only canon is unfalsifiable; lower tiers
        already churn under normal validation).
      * Not currently DEPRECATED.
      * ``_verify_score`` > 0 (some signal that re-test is warranted).

    Returns at most ``limit`` rows, sorted by descending verify score.
    Honors the ``MEMEE_NO_VERIFY`` / ``MEMEE_QUIET`` kill switches:
    suppressed → empty list.
    """
    if _layer07_suppressed():
        return []
    if limit <= 0:
        return []

    now = now or datetime.now(timezone.utc)

    # Pull the canon pool first; in production this is a small set
    # relative to the memory store (canon is the top tier of the
    # maturity ladder). Sort + cap is fine in Python at this size.
    canon = (
        session.query(Memory)
        .filter(Memory.maturity == MaturityLevel.CANON.value)
        .all()
    )
    scored = [(m, _verify_score(m, now)) for m in canon]
    scored = [(m, s) for m, s in scored if s > 0]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [m for m, _s in scored[:limit]]


def verify_limit_from_env() -> int:
    """Read ``MEMEE_VERIFY_MAX_BULLETS`` (default 2). Invalid → default."""
    raw = os.environ.get("MEMEE_VERIFY_MAX_BULLETS")
    if raw is None:
        return VERIFY_DEFAULT_LIMIT
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return VERIFY_DEFAULT_LIMIT
    return max(0, n)
