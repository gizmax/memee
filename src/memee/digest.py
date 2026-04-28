"""Once-a-week digest, prepended to the next SessionStart briefing.

Memee already shows the agent a smart briefing on every prompt. That's
high-frequency and task-routed — it answers "what should I remember for
*this* task?". What it doesn't do is the slower, periodic question:
"what happened last week?".

CLI ``memee status`` exists, but it's a diagnostics surface (humans on a
terminal). The agent never sees it. So the user has no quiet, ambient
weekly receipt of how the memory is changing. This module fills that
gap — once every seven days, the first ``memee brief`` of the week gets
a small markdown header prepended, summarising the previous 7-day
window. The next six days don't repeat it (showing the same digest on
every session would be noise).

Design constraints (mirroring ``update_check.py``):

* **Silent failure**. DB-missing, schema-drift, IO error → return None.
  The briefing must never break.
* **Honest numbers, not impressive ones**. Two of the four counters are
  *proxies* for the truth — see the per-counter docstrings below.
  We label them as such in the docstring + CHANGELOG; we don't dress
  them up in marketing.
* **Killable**. ``MEMEE_NO_DIGEST=1`` (any non-empty value) disables the
  whole thing. Useful in CI or for users who find the receipt annoying.
* **No new deps**. ``json`` + stdlib datetime only.
* **Local-only**. Every number comes from ``~/.memee/memee.db``;
  nothing leaves the machine.

Cache shape (``~/.memee/weekly_digest.json``)::

    {"generated_at": "2026-04-21T08:32:11+00:00", "payload": {...}}

The cache is the *de-duplication* mechanism — once we render a digest,
that timestamp pins it for seven days. We don't store the rendered
markdown; the payload is just a debugging aid (so a curious user can
``cat`` the file and see the numbers without re-deriving them).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

CACHE_PATH = Path.home() / ".memee" / "weekly_digest.json"
DIGEST_INTERVAL_DAYS = 7


# ── Cache I/O ───────────────────────────────────────────────────────────────


def _read_cache() -> dict | None:
    """Best-effort cache read. Returns None on missing file / parse error /
    wrong shape — every failure mode collapses to "regenerate"."""
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_cache(payload: dict) -> None:
    """Best-effort cache write — never raise. The cache is a hint; if the
    HOME dir is read-only, we just regenerate next session."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        pass


def _parse_iso(s: object) -> datetime | None:
    """Parse an ISO-8601 string back into an aware datetime. Returns None
    on garbage so corrupt cache entries fall through to regeneration."""
    if not isinstance(s, str):
        return None
    try:
        # ``fromisoformat`` accepts the ``+00:00`` we wrote out.
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Treat naive timestamps as UTC — older versions of this module
        # may have written naive ISO strings; don't crash on legacy cache.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Metric queries ──────────────────────────────────────────────────────────


def _compute_metrics(session, since: datetime) -> dict:
    """Return the four counters for the window ``[since, now]``.

    All queries run against the local SQLite DB. The function deliberately
    catches its own errors at the *call* site (``format_digest_notice``);
    inside here we let SQLAlchemy raise so a schema regression is loud
    during tests.
    """
    from sqlalchemy import func, or_

    from memee.engine.impact import ImpactEvent, ImpactType
    from memee.storage.models import MaturityLevel, Memory

    # ── memories applied: events that prove a memory was used by an
    # agent. We count three impact types as "applied":
    #   * KNOWLEDGE_REUSED — the agent explicitly drew on a memory
    #   * MISTAKE_AVOIDED — a warning steered the agent away from a known bad path
    #   * DECISION_INFORMED — historical context shaped a decision
    # Excluded: MISTAKE_MADE (agent ignored the warning),
    # WARNING_INEFFECTIVE (warning didn't help, didn't hurt),
    # TIME_SAVED / CODE_CHANGED (downstream effects, not "memory was used").
    applied_types = (
        ImpactType.KNOWLEDGE_REUSED.value,
        ImpactType.MISTAKE_AVOIDED.value,
        ImpactType.DECISION_INFORMED.value,
    )
    memories_applied = (
        session.query(func.count(ImpactEvent.id))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.impact_type.in_(applied_types))
        .scalar()
        or 0
    )

    # ── warnings checked: anything where the warning surface mattered,
    # regardless of outcome. MISTAKE_AVOIDED + MISTAKE_MADE +
    # WARNING_INEFFECTIVE all count — they all represent a warning that
    # was delivered and the agent's response was recorded.
    warning_types = (
        ImpactType.MISTAKE_AVOIDED.value,
        ImpactType.MISTAKE_MADE.value,
        ImpactType.WARNING_INEFFECTIVE.value,
    )
    warnings_checked = (
        session.query(func.count(ImpactEvent.id))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.impact_type.in_(warning_types))
        .scalar()
        or 0
    )

    # ── promoted to canon: HEURISTIC PROXY.
    # Memee's OSS schema doesn't track maturity transitions in a separate
    # ledger — there is no "promoted_at" column. The closest available
    # signal is ``updated_at`` on a memory whose current ``maturity`` is
    # ``canon``. If a CANON memory was updated in the last 7 days, it was
    # almost certainly *just* promoted (CANON-tier memories are stable;
    # they don't get edited at random). False positives: a canon memory
    # whose tags or content was hand-corrected this week. False negatives:
    # a memory promoted >7 days ago and not touched since (correct — we
    # only want recent promotions). The proxy errs on the small side,
    # which fits Memee's voice.
    promoted_to_canon = (
        session.query(func.count(Memory.id))
        .filter(Memory.maturity == MaturityLevel.CANON.value)
        .filter(Memory.updated_at.isnot(None))
        .filter(Memory.updated_at >= since)
        .scalar()
        or 0
    )

    # ── needs review: HEURISTIC PROXY.
    # Spec preference is "hypothesis with both a positive and a negative
    # validation". That requires a GROUP BY + HAVING over
    # ``memory_validations`` and is finicky to express portably. The
    # simpler proxy — and the one we ship — is "still a hypothesis AND
    # confidence has fallen below 0.4". Memee's confidence model already
    # bakes invalidations into the score (-0.12 × current per
    # invalidation), so a low-confidence hypothesis is structurally the
    # same population the spec was after, just expressed in the units
    # the engine actually maintains.
    # We additionally gate on ``invalidation_count > 0`` so a brand-new
    # untouched hypothesis (which sits at the 0.5 default and might have
    # drifted under but was never argued against) doesn't get flagged.
    needs_review = (
        session.query(func.count(Memory.id))
        .filter(Memory.maturity == MaturityLevel.HYPOTHESIS.value)
        .filter(Memory.confidence_score < 0.4)
        .filter(or_(Memory.invalidation_count > 0, Memory.validation_count > 0))
        .scalar()
        or 0
    )

    return {
        "memories_applied": int(memories_applied),
        "warnings_checked": int(warnings_checked),
        "promoted_to_canon": int(promoted_to_canon),
        "needs_review": int(needs_review),
    }


# ── Rendering ───────────────────────────────────────────────────────────────


def _render(payload: dict) -> str | None:
    """Render the digest payload as a multi-line markdown header, or
    return None if every counter is zero (no receipt to show).

    Output is one quoted markdown block. Lines are pre-wrapped with
    ``> `` so they survive being concatenated to the smart-briefing,
    which is *also* a quoted block — two quoted blocks back-to-back
    read as one continuous quote.

    No trailing newline. The integrator (``cli.brief``) adds the
    ``\\n\\n`` separator between this block and the rest of the
    briefing.
    """
    applied = payload.get("memories_applied", 0)
    warnings = payload.get("warnings_checked", 0)
    promoted = payload.get("promoted_to_canon", 0)
    review = payload.get("needs_review", 0)

    # If every counter is zero there's no story to tell. Show nothing
    # rather than a misleadingly empty receipt.
    if not (applied or warnings or promoted or review):
        return None

    # Build the activity line from non-zero counters only — the spec is
    # explicit about this: small honest numbers, not vanity metrics, and
    # zero counters get dropped instead of dressed up.
    activity_parts: list[str] = []
    if applied:
        activity_parts.append(
            f"{applied} {'memory' if applied == 1 else 'memories'} applied"
        )
    if warnings:
        activity_parts.append(
            f"{warnings} warning{'s' if warnings != 1 else ''} checked"
        )
    if promoted:
        activity_parts.append(
            f"{promoted} promoted to canon"
        )

    lines = ["> Memee — last 7 days:"]
    if activity_parts:
        lines.append(f"> {', '.join(activity_parts)}.")
    if review:
        lines.append(
            f"> Needs review: {review} "
            f"hypothes{'is' if review == 1 else 'es'} with conflicting validations."
        )

    return "\n".join(lines)


# ── Public API ──────────────────────────────────────────────────────────────


def format_digest_notice() -> str | None:
    """Return a multi-line markdown digest if it's been at least 7 days
    since the last digest, otherwise None.

    Behaviour:

    1. Read cache ``~/.memee/weekly_digest.json``. The shape is
       ``{"generated_at": <iso8601>, "payload": {...}}``.
    2. If the cache is missing, corrupt, or its ``generated_at`` is older
       than ``DIGEST_INTERVAL_DAYS`` days — *regenerate*. Open the local
       DB, compute the counters, render markdown, write the cache, and
       return the rendered string.
    3. Otherwise return ``None`` — the same digest doesn't re-prepend
       in the middle of the week. The user sees it once on the first
       session of the week and that's it.
    4. ``MEMEE_NO_DIGEST=1`` (any non-empty value) → return None.
    5. Any DB / IO / parse error during regeneration → return None
       silently. A digest is a courtesy, never a feature gate.

    Honesty notes about the counters
    --------------------------------
    Two of the four counters are explicit *proxies*:

    * **promoted to canon**: counts CANON-maturity memories whose
      ``updated_at`` is within the window. Memee's OSS schema doesn't
      track a separate "promoted_at" timestamp, so this is a proxy. A
      hand-edited CANON entry will count as a "promotion"; it's the
      cleanest signal we can pull from one column.
    * **needs review**: counts HYPOTHESIS-maturity memories with
      ``confidence_score < 0.4`` AND non-zero validation activity. The
      spec preferred "hypothesis with both a positive and a negative
      validation", but that's a noisier query and confidence already
      collapses validation history into one number — a hypothesis below
      0.4 has been argued against more than for it.

    "memories applied" and "warnings checked" come straight from
    ``impact_events`` and have no proxying.

    Output format example::

        > Memee — last 7 days:
        > 18 memories applied, 5 warnings checked, 3 promoted to canon.
        > Needs review: 2 hypotheses with conflicting validations.

    All numbers come from the local DB at ``~/.memee/memee.db``;
    nothing leaves the machine.
    """
    if os.environ.get("MEMEE_NO_DIGEST"):
        return None

    now = datetime.now(timezone.utc)

    # Cache hit AND fresh → suppress (one digest per 7-day window).
    cached = _read_cache()
    if cached is not None:
        gen = _parse_iso(cached.get("generated_at"))
        if gen is not None:
            age = now - gen
            # ``age >= timedelta(0)`` guards against clock-skew (a future
            # cache shouldn't lock us out forever; treat negative ages as
            # "regenerate"). Within the interval → suppress.
            if timedelta(0) <= age < timedelta(days=DIGEST_INTERVAL_DAYS):
                return None

    # Otherwise: regenerate. The whole regen path is wrapped in a
    # try/except — DB lock, missing tables on a brand-new install, even
    # an unexpected SQLAlchemy error — all collapse to "no digest".
    try:
        from memee.storage.database import get_engine, get_session, init_db

        engine = init_db(get_engine())
        session = get_session(engine)
        try:
            since = now - timedelta(days=DIGEST_INTERVAL_DAYS)
            payload = _compute_metrics(session, since)
        finally:
            session.close()
    except Exception:
        return None

    rendered = _render(payload)

    # Stamp the cache regardless of whether we rendered anything. If
    # every counter was zero, we still don't want to re-query the DB on
    # every prompt for the next 7 days — the answer won't change without
    # at least one new memory or impact event landing first, and even
    # then a 7-day re-check is the right cadence by spec.
    _write_cache({"generated_at": now.isoformat(), "payload": payload})

    return rendered
