"""Adaptive Confidence Scoring and maturity promotion engine.

NOT Bayesian — it's a weighted moving average with bonus multipliers:
  - Cross-project bonus: 1.5x (proven in different context)
  - Model diversity bonus: 1.3x (different model family agrees)
  - Combined: 1.95x (cross-project × cross-model)

Confidence includes uncertainty estimate:
  uncertainty = 1 / sqrt(validation_count + invalidation_count + 1)
  → decreases as more evidence accumulates
"""

from __future__ import annotations

import math

from memee import config
from memee.engine.models import get_model_family
from memee.storage.models import MaturityLevel, Memory


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

    if validated:
        weight = s.validation_weight

        # Cross-project bonus
        if is_new_project:
            weight *= s.cross_project_bonus
        else:
            # Diminishing returns for same-project — read from denormalized dict
            counts = dict(memory.same_project_val_counts or {})
            same_count = counts.get(project_id, 0) if project_id else 0
            weight *= s.diminishing_factor ** same_count
            if project_id:
                counts[project_id] = same_count + 1
                memory.same_project_val_counts = counts

        # Model diversity bonus
        if is_new_model:
            weight *= s.cross_model_bonus

        # Asymptotic approach to 1.0
        memory.confidence_score = min(
            0.99,
            memory.confidence_score + weight * (1 - memory.confidence_score),
        )
        memory.validation_count = (memory.validation_count or 0) + 1

        # Track which projects we've seen (for cross-project bonus next time)
        if project_id and is_new_project:
            validated_ids.append(project_id)
            memory.validated_project_ids = list(validated_ids)
    else:
        # Invalidation: proportional decay
        memory.confidence_score = max(
            0.01,
            memory.confidence_score
            - s.invalidation_weight * memory.confidence_score,
        )
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
    memory.maturity = evaluate_maturity(memory)
    return memory.confidence_score


def get_uncertainty(memory: Memory) -> float:
    """Get uncertainty estimate for a memory's confidence.

    uncertainty = 1 / sqrt(total_evidence + 1)
    → High when few validations, low when many
    → Range: 1.0 (no evidence) to ~0.03 (1000 validations)
    """
    total_evidence = (memory.validation_count or 0) + (memory.invalidation_count or 0)
    return 1.0 / math.sqrt(total_evidence + 1)


def get_confidence_interval(memory: Memory) -> tuple[float, float]:
    """Get confidence interval: (lower, upper).

    confidence ± uncertainty, clamped to [0, 1].
    """
    conf = memory.confidence_score
    unc = get_uncertainty(memory)
    return (max(0.0, conf - unc), min(1.0, conf + unc))


def evaluate_maturity(memory: Memory) -> str:
    """Evaluate maturity level based on confidence + breadth.

    Progression: hypothesis → tested → validated → canon → deprecated

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

    # LLM quarantine gate: require DIVERSITY evidence before promotion.
    is_llm_sourced = memory.source_type == "llm"
    quarantine_lifted = (
        (memory.model_count or 0) >= 2           # Cross-model validation
        or (memory.project_count or 0) >= 2      # Cross-project validation
    )
    # Canon quarantine is stricter: cross-model required.
    canon_quarantine_lifted = (memory.model_count or 0) >= 2

    # Canon: high confidence + broad cross-project validation.
    # LLM sources additionally require cross-model evidence.
    if (
        c >= s.canon_min_confidence
        and memory.project_count >= s.canon_min_projects
        and memory.validation_count >= s.canon_min_validations
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
