"""Adaptive Confidence Scoring and maturity promotion engine.

**v2.4.0 (Tier 1.2 — `docs/memee-2026-roadmap.md`)**: Bayesian
Beta-Binomial posterior.

Each Memory carries two Float columns ``alpha`` and ``beta`` that
count *weighted* validation and invalidation evidence respectively.
The conjugate Beta(α, β) prior makes every update a closed-form
posterior:

  * Default prior: Beta(1, 1) — uniform, mean = 0.5 (matches the
    pre-v2.4.0 ``default=0.5`` on ``confidence_score``).
  * Validation event with evidence weight ``w``: ``α += w``.
  * Invalidation event with evidence weight ``w``: ``β += w``.
  * Posterior mean (= ``confidence_score``): ``α / (α + β)``.
  * Posterior variance: ``α·β / ((α+β)² · (α+β+1))``.
  * 95 % credible interval: Beta-distribution quantiles
    (``scipy.stats.beta.ppf`` if available, normal-approximation
    fallback otherwise).

The evidence weight ``w`` folds in:

  * **Cross-project bonus**: ``×s.cross_project_bonus`` (1.5) when
    the validator's project is new to this memory.
  * **Cross-model bonus**: ``×s.cross_model_bonus`` (1.3) when the
    validator's model family hasn't validated this memory before.
  * **Diminishing returns**: ``×s.diminishing_factor^same_count``
    on repeat same-project validation. Stops a chatty agent from
    promoting its own opinion.
  * **Combined ceiling**: ~×1.95 for a fresh-project + new-family
    validator, same as the pre-v2.4.0 stacking — but applied to
    evidence counts, not to a score nudge. Mathematically clean and
    bounds runaway confidence growth.

Why the rewrite (math/stats dossier + internal code review):
  * Two same-project validations of the same scope used to bump
    ``conf`` by *different absolute amounts* (proportional decay
    via ``conf + w·(1-conf)``). Beta-Binomial fixes this by
    expressing evidence as additive counts on α/β.
  * ``get_confidence_interval`` was ``conf ± 1/√(n+1)`` — has no
    statistical coverage guarantee. Beta quantiles do.
  * Cross-project / cross-model bonuses are now prior-strength
    adjustments on evidence, not score multipliers — invariant
    to ordering of the same evidence stream.

Backward compatibility:
  * ``confidence_score`` column stays populated as the posterior
    mean. Every callsite that reads it sees the same shape.
  * Legacy rows with ``alpha=beta=1.0`` and a non-trivial
    ``validation_count`` are back-filled at first read by
    ``_backfill_alpha_beta`` using ``α = conf·n + 1``,
    ``β = (1-conf)·n + 1`` — same posterior mean, evidence
    preserved.
  * ``get_uncertainty`` and ``get_confidence_interval`` keep their
    signatures; their internals delegate to the Beta posterior.

References:
  * Bayes Rules! ch. 3 — Beta-Binomial conjugacy
  * Paun et al. 2018 — Bayesian hierarchical Dawid-Skene
"""

from __future__ import annotations

import math
import os
from datetime import datetime

from memee import config
from memee.engine.models import get_model_family
from memee.storage.models import MaturityLevel, Memory


def _backfill_alpha_beta(memory: Memory) -> None:
    """Map legacy ``confidence_score`` + evidence counts to (α, β).

    Runs whenever a row still has α/β at the schema default (1.0, 1.0)
    *and* has accumulated validation history. Choosing
    ``α = conf·n + 1`` and ``β = (1-conf)·n + 1`` makes
    ``α/(α+β) = conf`` exactly while preserving the right amount of
    evidence (``α + β - 2 = n``).

    v2.4.2 simplification: the previous per-instance flag was a perf
    optimisation to skip the cheap n-check on hot paths. It caused a
    silent stale-state bug — if a caller mutated
    ``validation_count`` directly after the first evaluate, the
    backfill never re-ran. The two clamp checks below cost <1µs each
    so the flag isn't worth the foot-gun.
    """
    n = (memory.validation_count or 0) + (memory.invalidation_count or 0)
    if n <= 0:
        return
    if (memory.alpha or 0) > 1.0 + 1e-9 or (memory.beta or 0) > 1.0 + 1e-9:
        # Already migrated — nothing to do.
        return
    conf = float(memory.confidence_score or 0.5)
    memory.alpha = max(1.0, conf * n + 1.0)
    memory.beta = max(1.0, (1.0 - conf) * n + 1.0)


def _posterior_mean(memory: Memory) -> float:
    """Derive ``confidence_score`` from current (α, β)."""
    a = float(memory.alpha or 1.0)
    b = float(memory.beta or 1.0)
    return a / (a + b)


# ── FSRS-light per-memory decay (v2.4.5 — Tier 1.5) ────────────────
#
# Each Memory carries its own half-life. Predicted recall at time t is
# R(t) = 2^(-Δt/h) where Δt is days since ``last_retrieved``. On every
# successful validation we stretch h proportional to the "miss" the
# memory was about to incur — recall itself consolidates the trace
# (Wozniak SM-2 1990; FSRS / Ye et al. KDD 2022, Anki 23.10+ default).
#
# Constants are tuned to FSRS' published defaults at smaller scale:
#   * FSRS_GROWTH  — multiplicative bonus on successful recall. 1+γ·(1-R)
#                    so a hot memory (R~1) barely grows, a stale memory
#                    (R~0) doubles. γ=0.5 matches the conservative
#                    "growth" param FSRS users settle at.
#   * FSRS_SHRINK  — multiplicative penalty on invalidation. h *= 0.6
#                    halves a hot memory's half-life every two negative
#                    events. Matches FSRS' lapse-stability heuristic.
#   * FSRS_MIN_HL  — never drop below 1 day; deprecation belongs to
#                    SPRT (v2.4.2), not exponential collapse.
#   * FSRS_MAX_HL  — cap at 365d; institutional memory doesn't go
#                    permastore (Bahrick 1984) — operators eventually
#                    revisit even canon.
FSRS_GROWTH = 0.5
FSRS_SHRINK = 0.6
FSRS_MIN_HL = 1.0
FSRS_MAX_HL = 365.0
FSRS_DEFAULT_HL = 14.0


def predicted_retrievability(memory: Memory, now: datetime | None = None) -> float:
    """Return ``R(t) = 2^(-Δt/h)`` — the FSRS predicted recall.

    1.0 means "just retrieved, perfectly recalled". 0.5 means "one
    half-life elapsed". As ``Δt → ∞`` the function approaches 0.
    Clamped at [0, 1].

    When ``last_retrieved`` is NULL (memory never surfaced via search /
    brief / verify), we fall back to ``created_at`` — the closest
    analogue to "the trace was laid down then".
    """
    from datetime import datetime, timezone

    raw_h = memory.half_life
    h = float(raw_h if raw_h is not None else FSRS_DEFAULT_HL)
    if h <= 0:
        return 1.0
    # Fallback chain mirrors "most recent meaningful engagement":
    # last_retrieved (v2.4.5+ — every search/brief/verify event) →
    # last_applied_at (every successful application) →
    # last_validated_at (every validate/invalidate event) →
    # created_at (memory was at least laid down). Anchoring to the
    # latest signal lets legacy rows (pre-v2.4.5, no last_retrieved)
    # still get a coherent decay reading.
    anchor = (
        memory.last_retrieved
        or memory.last_applied_at
        or memory.last_validated_at
        or memory.created_at
    )
    if anchor is None:
        return 1.0
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta_days = (now - anchor).total_seconds() / 86400.0
    if delta_days <= 0:
        return 1.0
    return max(0.0, min(1.0, 2.0 ** (-delta_days / h)))


def _fsrs_update_on_validation(memory: Memory, *, validated: bool) -> None:
    """Update ``half_life`` + ``last_retrieved`` after a validation event.

    Successful recall consolidates the trace: ``h ← h · (1 + γ·(1-R))``.
    The (1-R) factor means a "barely-remembered" memory (low R) earns
    a big half-life bump, while a "freshly seen" memory (R≈1) barely
    grows — matches the SM-2 / FSRS finding that *effortful* recall
    is what consolidates.

    Invalidation shrinks: ``h ← h · 0.6``. A wrong call is evidence
    the trace was weaker than predicted; collapse the curve.

    Always stamp ``last_retrieved = now`` because the agent DID
    surface the memory — even an invalidation is a retrieval event.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    h = float(memory.half_life or FSRS_DEFAULT_HL)
    R = predicted_retrievability(memory, now=now)

    if validated:
        new_h = h * (1.0 + FSRS_GROWTH * (1.0 - R))
    else:
        new_h = h * FSRS_SHRINK

    memory.half_life = max(FSRS_MIN_HL, min(FSRS_MAX_HL, new_h))
    memory.last_retrieved = now


def update_confidence(
    memory: Memory,
    validated: bool,
    project_id: str | None = None,
    model_name: str | None = None,
) -> float:
    """Update memory confidence score.

    Bonus stacking:
      Same project, same model:        ×1.0 (base)
      Same project, different model:    ×1.3 (model diversity — first time a new family validates)
      Different project, same model:    ×1.5 (cross-project)
      Different project + model:        ×1.95 (combined)

    The cross-model bonus fires only the first time each model family
    validates this memory. Repeat validations from an already-seen family
    (including the original author's family) get no diversity bonus.

    Returns the updated confidence_score.
    """
    s = config.settings

    # FAST PATH: use denormalized project set, not lazy-loaded relationships.
    # One-time backfill per Python object: if denormalized list is empty but
    # ProjectMemory rows exist, seed once. `_memee_projects_backfilled` on the
    # instance guards against re-running the query on memories that legitimately
    # have no project links yet (Bug 1: profile showed ~53% of calls re-querying).
    validated_ids = list(memory.validated_project_ids or [])
    if (
        not validated_ids
        and memory.id is not None
        and not getattr(memory, "_memee_projects_backfilled", False)
    ):
        try:
            from memee.storage.models import ProjectMemory
            from sqlalchemy import inspect as _inspect
            sess = _inspect(memory).session
            if sess is not None:
                existing = sess.query(ProjectMemory.project_id).filter(
                    ProjectMemory.memory_id == memory.id
                ).all()
                validated_ids = [row[0] for row in existing]
                if validated_ids:
                    memory.validated_project_ids = validated_ids
        except Exception:
            pass
        # Mark as backfilled regardless — either we seeded, or there was nothing
        # to seed. Either way we won't re-query this instance. Survives
        # session.expire() of ORM columns because it's a plain Python attr.
        try:
            memory._memee_projects_backfilled = True
        except Exception:
            pass

    is_new_project = project_id is not None and project_id not in validated_ids

    # Determine `is_new_model`: does this validator bring a family we haven't
    # seen before? We track families both from the AUTHOR's source_model (so a
    # same-family self-validation correctly gets no bonus) and from every
    # prior validator. The bonus fires only the FIRST time each family touches
    # this memory; repeat validations by any already-seen family get no bonus.
    source_family = get_model_family(memory.source_model) if memory.source_model else "unknown"
    families_seen_snapshot = set(memory.model_families_seen or [])
    if source_family != "unknown":
        families_seen_snapshot.add(source_family)
    new_family = get_model_family(model_name) if model_name else "unknown"
    is_new_model = bool(model_name) and new_family != "unknown" and new_family not in families_seen_snapshot

    # v2.4.0: back-fill α/β from legacy (conf, n) on first touch.
    _backfill_alpha_beta(memory)

    # Compute evidence weight ``w`` that captures the same scope/source
    # signal the pre-v2.4.0 model wove into ``conf + w·(1-conf)`` — but
    # now applied as additive evidence on α (validations) or β
    # (invalidations). Same ceiling (~1.95 for cross-project + cross-
    # model) because the bonus factors are unchanged; the difference is
    # that *order* of identical evidence no longer affects the posterior.
    if validated:
        weight = 1.0  # one evidence count, unweighted base

        if is_new_project:
            weight *= s.cross_project_bonus
        else:
            counts = dict(memory.same_project_val_counts or {})
            same_count = counts.get(project_id, 0) if project_id else 0
            weight *= s.diminishing_factor ** same_count
            if project_id:
                counts[project_id] = same_count + 1
                memory.same_project_val_counts = counts

        if is_new_model:
            weight *= s.cross_model_bonus

        # Posterior update: α += w. confidence_score is the derived mean.
        memory.alpha = float(memory.alpha or 1.0) + weight
        memory.confidence_score = max(0.01, min(0.99, _posterior_mean(memory)))
        memory.validation_count = (memory.validation_count or 0) + 1

        if project_id and is_new_project:
            validated_ids.append(project_id)
            memory.validated_project_ids = list(validated_ids)
    else:
        # Invalidation: β += w. Weighting mirrors validation so a
        # cross-project negative carries as much evidence as a cross-
        # project positive — the asymmetric ``-0.12·conf`` decay of the
        # pre-v2.4.0 rule was a hand-tuned magic number with no
        # statistical justification.
        weight = 1.0
        if is_new_project:
            weight *= s.cross_project_bonus
        if is_new_model:
            weight *= s.cross_model_bonus

        memory.beta = float(memory.beta or 1.0) + weight
        memory.confidence_score = max(0.01, min(0.99, _posterior_mean(memory)))
        memory.invalidation_count = (memory.invalidation_count or 0) + 1

    if is_new_project:
        memory.project_count = (memory.project_count or 0) + 1

    # Track unique VALIDATOR model families via denormalized list.
    # Bug 4 fix: only validators accrue into model_families_seen. The author's
    # source_model is author metadata, NOT validator evidence — seeding from it
    # would let a single cross-family validator lift the LLM quarantine gate
    # (model_count=1 from author + 1 validator = 2), which isn't the promised
    # "two validators of different families" defense.
    #
    # Bug 1 fix: one-time backfill from MemoryValidation rows, guarded by a
    # per-instance flag so memories that have no prior validations don't
    # re-query on every update_confidence call.
    if validated and model_name and new_family != "unknown":
        families_list = list(memory.model_families_seen or [])

        if not getattr(memory, "_memee_families_backfilled", False):
            # Only query if the memory is persisted AND had prior validations
            # (exclude THIS one — it's already reflected in validation_count).
            prior_validations = (memory.validation_count or 0) - 1
            if prior_validations > 0 and memory.id is not None:
                try:
                    from memee.storage.models import MemoryValidation
                    from sqlalchemy import inspect as _inspect
                    sess = _inspect(memory).session
                    if sess is not None:
                        rows = sess.query(MemoryValidation.validator_model).filter(
                            MemoryValidation.memory_id == memory.id
                        ).all()
                        seen = set(families_list)
                        for (vm,) in rows:
                            if vm:
                                fam = get_model_family(vm)
                                if fam != "unknown" and fam not in seen:
                                    seen.add(fam)
                                    families_list.append(fam)
                except Exception:
                    pass
            try:
                memory._memee_families_backfilled = True
            except Exception:
                pass

        # Persist the combined "source + validators" family set. Tests and
        # the LLM quarantine gate both read `model_count` as "distinct
        # families that touched this memory including the author".
        changed = False
        if new_family not in families_list:
            families_list.append(new_family)
            changed = True
        if source_family != "unknown" and source_family not in families_list:
            families_list.append(source_family)
            changed = True
        if changed or memory.model_count != len(families_list):
            memory.model_families_seen = families_list
            memory.model_count = len(families_list)

    # Bug 3 fix: application_count counts successful APPLICATIONS, not
    # invalidations. A user saying "this didn't work" is not proof the agent
    # applied the memory in a new context — bumping application_count on
    # invalidation caused auto-deprecation to fire eagerly on memories with
    # many invalidations and few real applications.
    if validated:
        memory.application_count = (memory.application_count or 0) + 1

    # v2.4.5: per-memory FSRS half-life update. Stretches on success,
    # shrinks on failure. Layer 0.7 reads ``predicted_retrievability``
    # to decide which canon needs re-checking.
    _fsrs_update_on_validation(memory, validated=validated)

    memory.maturity = evaluate_maturity(memory)
    return memory.confidence_score


def get_uncertainty(memory: Memory) -> float:
    """Get uncertainty estimate for a memory's confidence.

    v2.4.0: returns the *standard deviation* of the Beta(α, β)
    posterior — the principled successor to the pre-v2.4.0
    ``1/√(n+1)`` heuristic.

    For Beta(α, β):
      σ² = α·β / ((α+β)² · (α+β+1))

    At Beta(1, 1) (no evidence) the SD ≈ 0.289, matching "high
    uncertainty when fresh". As evidence accumulates, SD shrinks
    toward 0 at the same rate as the legacy formula but with proper
    statistical interpretation.
    """
    a = float(memory.alpha or 1.0)
    b = float(memory.beta or 1.0)
    total = a + b
    if total <= 0:
        return 0.5
    variance = (a * b) / ((total * total) * (total + 1.0))
    return math.sqrt(max(0.0, variance))


def confidence_hdi(memory: Memory, level: float = 0.95) -> tuple[float, float, float]:
    """Return ``(lower, mean, upper)`` for the Beta posterior at ``level``.

    v2.4.0. Uses ``scipy.stats.beta.ppf`` when available; falls back to
    a normal-approximation ``mean ± z·σ`` otherwise so the function
    works even on installs that skipped scientific extras.

    ``level=0.95`` → 95 % credible interval (standard). Pass smaller
    values (e.g. 0.5) for the interquartile range. Result is clamped
    to [0, 1] so callers can plot it directly.
    """
    a = float(memory.alpha or 1.0)
    b = float(memory.beta or 1.0)
    mean = a / (a + b)
    alpha_tail = (1.0 - level) / 2.0

    try:
        from scipy.stats import beta as _beta_dist  # type: ignore[import-not-found]
        lo = float(_beta_dist.ppf(alpha_tail, a, b))
        hi = float(_beta_dist.ppf(1.0 - alpha_tail, a, b))
    except ImportError:
        # Normal approximation: σ from the same variance formula as
        # get_uncertainty(). z = 1.96 for level=0.95; compute generally
        # from the tail probability via the inverse-erf approximation.
        # Wichura 1988 inverse-CDF would be overkill — math.erfinv
        # works fine and ships with stdlib (Python 3.11+ math module).
        sigma = get_uncertainty(memory)
        # Inverse normal CDF: z = sqrt(2) · erfinv(2p - 1)
        z = math.sqrt(2.0) * math.erfinv(1.0 - 2.0 * alpha_tail)
        lo = mean - z * sigma
        hi = mean + z * sigma

    return (max(0.0, lo), mean, min(1.0, hi))


def get_confidence_interval(memory: Memory) -> tuple[float, float]:
    """Get the 95 % credible interval: ``(lower, upper)``.

    v2.4.0 backward-compat shim — same signature as the pre-v2.4.0
    ``conf ± 1/√(n+1)`` heuristic, but now backed by exact Beta
    quantiles. New code should prefer ``confidence_hdi`` which also
    returns the posterior mean.
    """
    lo, _mean, hi = confidence_hdi(memory)
    return (lo, hi)


# ── SPRT (v2.4.2 — Tier 1.3) ────────────────────────────────────────
#
# Wald's Sequential Probability Ratio Test for canon promotion +
# deprecation. Replaces the hand-tuned ``validation_count >= 10 AND
# confidence_score >= 0.85`` gate with a single Type-I/II error-rate
# dial that has a statistical guarantee.
#
# Hypotheses:
#   H0 (null):  p ≤ p0 = 0.5    (memory's true reliability is no better
#                                than chance — should NOT be canon)
#   H1 (alt):   p ≥ p1 = 0.85   (memory is canon-quality)
#
# Decision boundaries (Wald 1945, Wolfowitz optimality 1948):
#   Promote (accept H1) when LLR ≥ log((1-β)/α)  =  log(19)  ≈ +2.944
#   Deprecate (accept H0) when LLR ≤ log(β/(1-α)) = -log(19) ≈ -2.944
#   Continue sampling otherwise.
#
# Log-likelihood ratio of the Beta-Binomial evidence stream:
#   LLR = n_v · log(p1/p0)        + n_i · log((1-p1)/(1-p0))
#       = n_v · log(0.85/0.50)    + n_i · log(0.15/0.50)
#       = n_v · 0.5306             + n_i · (-1.2040)
#
# Per the v2.4.0 Beta-Binomial refactor, weighted-evidence counts live
# in α and β rather than the integer ``validation_count`` /
# ``invalidation_count`` columns (the integers are kept for back-compat
# but lose the cross-project / cross-model weighting). Use
# ``(α - prior_α, β - prior_β)`` so the prior Beta(1, 1) doesn't double-
# count as evidence.
#
# Reference: math/stats dossier §6 (docs/memee-2026-roadmap.md
# Tier 1.3). Anchors: Wald (1945, "Sequential tests of statistical
# hypotheses"); Wolfowitz 1948 optimality; Schönbrodt et al. BRM 2017
# (sequential Bayes factor — same family under flat priors).
_SPRT_P0 = 0.5
_SPRT_P1 = 0.85
_SPRT_ALPHA = 0.05  # Type-I error rate (false promotion)
_SPRT_BETA = 0.05   # Type-II error rate (false retention of stale)
_SPRT_LOG_RATIO_VAL = math.log(_SPRT_P1 / _SPRT_P0)            # ≈ +0.5306
_SPRT_LOG_RATIO_INV = math.log((1 - _SPRT_P1) / (1 - _SPRT_P0))  # ≈ -1.2040
_SPRT_UPPER = math.log((1 - _SPRT_BETA) / _SPRT_ALPHA)         # ≈ +2.944
_SPRT_LOWER = math.log(_SPRT_BETA / (1 - _SPRT_ALPHA))         # ≈ -2.944


def sprt_log_likelihood_ratio(memory: Memory) -> float:
    """Compute the SPRT log-likelihood ratio for a memory's evidence.

    Positive values lean toward H1 (canon-quality, p ≥ 0.85). Negative
    values lean toward H0 (chance-or-worse, p ≤ 0.5). The threshold
    constants ``_SPRT_UPPER`` and ``_SPRT_LOWER`` partition the LLR axis
    into "promote / continue / deprecate" regions with the configured
    Type-I/II error rates.
    """
    # Evidence counts = posterior shape minus prior Beta(1, 1). Negative
    # values shouldn't happen (priors are always 1.0) but clamp to be
    # safe against legacy rows that haven't been back-filled yet.
    n_val = max(0.0, float(memory.alpha or 1.0) - 1.0)
    n_inv = max(0.0, float(memory.beta or 1.0) - 1.0)
    return n_val * _SPRT_LOG_RATIO_VAL + n_inv * _SPRT_LOG_RATIO_INV


def _sprt_promotion_signal(memory: Memory) -> str:
    """Return ``"promote"`` / ``"deprecate"`` / ``"continue"``.

    Pure statistical signal — does NOT consult ``project_count``,
    ``model_count``, or LLM source. Diversity gates remain a separate
    check in :func:`evaluate_maturity`. Statistical evidence and
    diversity evidence are independent requirements for canon: both
    must hold.
    """
    llr = sprt_log_likelihood_ratio(memory)
    if llr >= _SPRT_UPPER:
        return "promote"
    if llr <= _SPRT_LOWER:
        return "deprecate"
    return "continue"


def _sprt_enabled() -> bool:
    """SPRT promotion is default-ON in v2.4.2.

    ``MEMEE_SPRT_PROMOTION=0`` falls back to the legacy ``c >= 0.85
    AND validation_count >= 10`` gate. Useful for benchmarking,
    debugging, or operators who want a fixed threshold for compliance
    auditing.
    """
    return os.environ.get("MEMEE_SPRT_PROMOTION", "1").strip().lower() not in (
        "0", "false", "no", "off", ""
    )


def evaluate_maturity(memory: Memory) -> str:
    """Evaluate maturity level based on confidence + breadth.

    Progression: hypothesis → tested → validated → canon → deprecated

    **v2.4.2 (Tier 1.3): SPRT-gated canon promotion.** Replaces the
    pre-v2.4.2 ``confidence_score >= 0.85 AND validation_count >= 10``
    gate with Wald's Sequential Probability Ratio Test. Promotes when
    the log-likelihood ratio crosses the upper Wald boundary
    (``LLR ≥ log((1-β)/α)``) for the calibrated Type-I/II error rates
    (default 5 %/5 %). Diversity requirements (project_count,
    model_count, LLM quarantine) remain *independent* gates: both
    statistical evidence and breadth-of-validation must hold for canon.

    Deprecation also gains an SPRT-driven path: a memory whose
    evidence stream crosses the *lower* Wald boundary auto-deprecates
    regardless of the historical confidence_score smoothing. Catches
    "long-tail canon that started failing under new conditions" that
    the ratio-only rule missed.

    Fall back to the legacy gate when ``MEMEE_SPRT_PROMOTION=0``.

    LLM-SOURCE QUARANTINE: memories with source_type='llm' stay below
    VALIDATED until DIVERSITY evidence lifts the gate:
      (a) ≥2 different model families validated it (cross-model), OR
      (b) ≥2 different projects validated it (cross-project).

    NOTE: raw validation_count is NOT sufficient. Repeated validation by the
    SAME model in the SAME project is exactly the hallucination
    self-reinforcement pathway we're defending against. The previous OR on
    `validation_count >= 3` let one chatty agent promote its own fabrication
    after three echoes — removed.

    CANON for LLM-sourced memories is stricter: cross-model evidence
    (model_count >= 2) is required in addition to the usual canon thresholds.
    """
    if memory.deprecated_at:
        return MaturityLevel.DEPRECATED.value

    # v2.4.2: defensive back-fill so callers that hit
    # ``evaluate_maturity`` directly (without going through
    # ``update_confidence``) still get the right SPRT signal on
    # legacy rows that haven't migrated their (conf, n) → (α, β).
    _backfill_alpha_beta(memory)

    c = memory.confidence_score
    s = config.settings

    # Auto-deprecate: low confidence after the memory has been tried enough
    # times OR been invalidated enough times to strongly indicate it's wrong.
    # Invalidations and applications are counted separately (application_count
    # only bumps on validated=True per Bug 3 fix), but either signal at the
    # deprecation threshold justifies retiring the memory.
    touch_events = (memory.application_count or 0) + (memory.invalidation_count or 0)
    if c < s.deprecated_max_confidence and touch_events >= s.deprecated_min_applications:
        return MaturityLevel.DEPRECATED.value

    # v2.4.2 (Tier 1.3): SPRT-driven deprecation. A memory whose
    # log-likelihood ratio falls below the lower Wald boundary has
    # accumulated statistical evidence that its true reliability is
    # ≤ p0 (chance). Auto-deprecate independent of the historical
    # confidence_score, catching "canon that started failing under
    # new conditions" the ratio-only rule misses. Toggleable via
    # ``MEMEE_SPRT_PROMOTION``.
    if _sprt_enabled() and _sprt_promotion_signal(memory) == "deprecate":
        return MaturityLevel.DEPRECATED.value

    # LLM quarantine gate: require DIVERSITY evidence before promotion.
    is_llm_sourced = memory.source_type == "llm"
    quarantine_lifted = (
        (memory.model_count or 0) >= 2           # Cross-model validation
        or (memory.project_count or 0) >= 2      # Cross-project validation
    )
    # Canon quarantine is stricter: cross-model required.
    canon_quarantine_lifted = (memory.model_count or 0) >= 2

    # Statistical evidence gate for canon. v2.4.2: SPRT replaces
    # the legacy ``c >= 0.85 AND validation_count >= 10`` rule with
    # Wald boundaries on the Beta-Binomial evidence stream. Diversity
    # gates (project_count, LLM quarantine) remain INDEPENDENT — both
    # must hold for canon. Legacy gate available behind feature flag.
    if _sprt_enabled():
        statistical_canon_gate = _sprt_promotion_signal(memory) == "promote"
    else:
        statistical_canon_gate = (
            c >= s.canon_min_confidence
            and memory.validation_count >= s.canon_min_validations
        )

    # Canon: statistical evidence (SPRT or legacy threshold) + broad
    # cross-project validation. LLM sources additionally require
    # cross-model evidence.
    if (
        statistical_canon_gate
        and memory.project_count >= s.canon_min_projects
        and (not is_llm_sourced or canon_quarantine_lifted)
    ):
        return MaturityLevel.CANON.value

    # Validated: good confidence + multiple projects.
    # LLM sources require quarantine to be lifted (cross-model OR cross-project).
    if (
        c >= s.validated_min_confidence
        and memory.project_count >= s.validated_min_projects
        and (not is_llm_sourced or quarantine_lifted)
    ):
        return MaturityLevel.VALIDATED.value

    # Tested: has been touched by at least one validation OR invalidation
    # event. "Tested" doesn't assert the memory works — it asserts the memory
    # has been exercised in the real world. An invalidation is as much proof
    # of exercise as an application. (LLM can reach tested, just not higher.)
    if touch_events >= s.tested_min_applications:
        return MaturityLevel.TESTED.value

    return MaturityLevel.HYPOTHESIS.value
