"""``memee pulse`` — retrospective drill-down over a recent window.

Where ``memee status`` is *synchronic* (totals, maturity histogram — what
the store looks like right now) and ``memee why`` is *targeted* (one
snippet → one canon hit), ``memee pulse`` is *diachronic*: "what did
Memee actually do in the last N days?". It's the receipt the user wants
when an agent just finished citing ``[mem:xxxxxxxx]`` in their transcript
and they want the broader story.

Three commands, three jobs, no overlap:

* ``memee status`` — synchronic dashboard (totals, maturity histogram,
  type breakdown). Diagnostics. Don't deprecate.
* ``memee why <snippet>``   — single-snippet drill-down. Returns the
  canon entries that match a specific code/question.
* ``memee pulse``           — recent activity. Top reuses, prevented
  mistakes, fresh canon, hypotheses needing review, ROI footer. Each
  bullet ends with a ``[mem:xxxxxxxx]`` cite token so a follow-up
  ``memee cite <hash>`` works one-shot.

Design constraints (mirrors ``digest.py`` and the M1/M2 receipt
surfaces):

* **Honest numbers, not impressive ones.** Two of the buckets are
  *proxies* — see the per-bucket docstrings. Same proxy definitions as
  ``digest.py`` so two surfaces don't drift.
* **Silent failure.** ``compute_pulse`` swallows every DB / IO /
  schema-drift error and returns the "quiet week" payload — empty lists
  + headline ``Memee was quiet this week.``. Never raises.
* **Soft dependency on receipts.py (M1).** If ``format_session_receipt``
  exists at ``memee.receipts``, we reuse it for the headline so the
  pulse and the in-conversation receipt phrase the same thing the same
  way. If it doesn't (sibling agent hasn't merged), we hand-format an
  equivalent one-liner from ``engine.impact`` counters.
* **No new deps.** ``json`` + stdlib datetime + SQLAlchemy.
* **Local-only.** Every number comes from ``~/.memee/memee.db``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Title truncation policy — same as session_ledger.format_session_summary
# and cli._truncate_title (60 chars + ``…``). Two surfaces converging on
# the same number keeps the receipts visually consistent.
TITLE_MAX_CHARS = 60

# Per-bucket caps. The product spec is firm on these; the renderer
# trusts them and doesn't re-truncate.
TOP_REUSED_CAP = 3
TOP_PREVENTED_CAP = 3
RECENT_CANON_CAP = 5
NEEDS_REVIEW_CAP = 10


# ── Helpers ─────────────────────────────────────────────────────────────


def _short_hash(memory_id: str) -> str:
    """First 8 hex chars of the UUID, dashes stripped — matches the
    ``[mem:xxxxxxxx]`` convention enforced by ``engine.citations``.

    Kept inline (not imported) so the pulse module stays loadable in
    error paths even if the engine import chain blew up.
    """
    if not memory_id:
        return ""
    return memory_id.replace("-", "")[:8]


def _cite(memory_id: str) -> str:
    """Render a citation token. ``[mem:?]`` if the id is empty so the
    bullet still parses on the agent side."""
    short = _short_hash(memory_id)
    return f"[mem:{short}]" if short else "[mem:?]"


def _truncate_title(title: str | None) -> str:
    """Cap a title at ``TITLE_MAX_CHARS`` with a trailing ellipsis.

    Mirrors ``cli._truncate_title`` so receipts read the same. ``None``
    or empty → ``"(untitled)"`` so the bullet has *something* to render.
    """
    if not title:
        return "(untitled)"
    title = title.strip()
    if len(title) <= TITLE_MAX_CHARS:
        return title
    return title[: TITLE_MAX_CHARS - 1].rstrip() + "…"


def _quiet_payload(
    days: int,
    since: datetime,
    until: datetime,
    headline: str = "Memee was quiet this week.",
) -> dict:
    """Build the canonical "nothing to see" payload — same shape, all
    lists empty, ROI zeroed. Callers in error paths return this so the
    consumer never needs ``.get(key, [])`` defensive branching."""
    return {
        "days": days,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "headline": headline,
        "top_reused": [],
        "top_prevented": [],
        "recent_canon": [],
        "needs_review": [],
        "time_saved_minutes": 0,
        "roi": None,
    }


# ── Headline ────────────────────────────────────────────────────────────


def _fallback_headline(impact_counts: dict, days: int) -> str:
    """One-liner equivalent to what ``format_session_receipt`` (M1) would
    produce, hand-rolled from ``engine.impact`` counters.

    Used when ``memee.receipts`` isn't on the import path yet (sibling M1
    agent hasn't merged). The wording is deliberately close to the
    digest's voice so two surfaces sound like one product:

        "Memee — last 7 days: 18 memories applied, 5 warnings checked."

    On a fully empty window we collapse to the canonical "quiet week"
    string so the renderer can short-circuit on it.
    """
    applied = int(impact_counts.get("memories_applied", 0))
    warnings = int(impact_counts.get("warnings_checked", 0))
    prevented = int(impact_counts.get("mistakes_prevented", 0))

    if not (applied or warnings or prevented):
        return "Memee was quiet this week."

    parts: list[str] = []
    if applied:
        parts.append(
            f"{applied} {'memory' if applied == 1 else 'memories'} applied"
        )
    if warnings:
        parts.append(
            f"{warnings} warning{'s' if warnings != 1 else ''} checked"
        )
    if prevented:
        parts.append(
            f"{prevented} mistake{'s' if prevented != 1 else ''} prevented"
        )

    window = "last 7 days" if days == 7 else f"last {days} days"
    return f"Memee — {window}: {', '.join(parts)}."


def _try_receipt_headline(impact_counts: dict, days: int) -> str | None:
    """Try the M1 receipt formatter; fall through to ``None`` if it isn't
    available or its signature differs.

    The contract with M1 isn't pinned (sibling agent owns that file) so
    we probe with several plausible call shapes and ignore everything
    except a non-empty string return value. Any exception → None.
    """
    try:
        from memee import receipts as _receipts  # type: ignore[attr-defined]
    except ImportError:
        return None
    except Exception:
        return None

    fn = getattr(_receipts, "format_session_receipt", None)
    if fn is None or not callable(fn):
        return None

    # Try a couple of plausible signatures. M1 may take a session/dict/
    # nothing — we don't want to bind to a specific shape and break if
    # the sibling agent picks a different one. Each attempt is wrapped
    # in its own try so a TypeError on one shape doesn't kill the rest.
    for kwargs in (
        {"impact": impact_counts, "days": days},
        {"counts": impact_counts, "days": days},
        {"impact_counts": impact_counts, "days": days},
        {"days": days},
        {},
    ):
        try:
            out = fn(**kwargs) if kwargs else fn()
        except TypeError:
            continue
        except Exception:
            return None
        if isinstance(out, str) and out.strip():
            return out.strip()
    return None


# ── Bucket queries ──────────────────────────────────────────────────────


def _query_top_reused(session, since: datetime) -> list[dict]:
    """Top reused memories in the window.

    Definition: memories with the most ``KNOWLEDGE_REUSED`` /
    ``DECISION_INFORMED`` / ``MISTAKE_AVOIDED`` impact events during the
    window. Same ``applied_types`` set the digest uses, so "memories
    applied" in the digest and "top reuses" here count the same
    population — just bucketed by memory id instead of summed.
    """
    from sqlalchemy import desc, func

    from memee.engine.impact import ImpactEvent, ImpactType
    from memee.storage.models import Memory

    applied_types = (
        ImpactType.KNOWLEDGE_REUSED.value,
        ImpactType.MISTAKE_AVOIDED.value,
        ImpactType.DECISION_INFORMED.value,
    )

    rows = (
        session.query(
            Memory.id,
            Memory.title,
            Memory.maturity,
            func.count(ImpactEvent.id).label("apply_count"),
        )
        .join(ImpactEvent, ImpactEvent.memory_id == Memory.id)
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.impact_type.in_(applied_types))
        .group_by(Memory.id, Memory.title, Memory.maturity)
        .order_by(desc("apply_count"), Memory.title)
        .limit(TOP_REUSED_CAP)
        .all()
    )

    return [
        {
            "mem_id": r.id,
            "title": r.title or "",
            "apply_count": int(r.apply_count or 0),
            "maturity": r.maturity or "",
            "cite": _cite(r.id),
        }
        for r in rows
    ]


def _query_top_prevented(session, since: datetime) -> list[dict]:
    """Top mistakes prevented in the window.

    Definition: distinct anti-pattern memories that produced a
    ``MISTAKE_AVOIDED`` impact event during the window. Sorted by event
    count (most-prevented first), ties broken by severity (critical >
    high > medium > low) and then memory title. We pull severity through
    the AntiPattern join — pattern memories never appear here.
    """
    from sqlalchemy import desc, func

    from memee.engine.impact import ImpactEvent, ImpactType
    from memee.storage.models import AntiPattern, Memory

    rows = (
        session.query(
            Memory.id,
            Memory.title,
            AntiPattern.severity,
            func.count(ImpactEvent.id).label("prevented_count"),
        )
        .join(ImpactEvent, ImpactEvent.memory_id == Memory.id)
        .join(AntiPattern, AntiPattern.memory_id == Memory.id)
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.impact_type == ImpactType.MISTAKE_AVOIDED.value)
        .group_by(Memory.id, Memory.title, AntiPattern.severity)
        .order_by(desc("prevented_count"), Memory.title)
        .limit(TOP_PREVENTED_CAP)
        .all()
    )

    return [
        {
            "mem_id": r.id,
            "title": r.title or "",
            "severity": r.severity or "",
            "cite": _cite(r.id),
        }
        for r in rows
    ]


def _query_recent_canon(session, since: datetime) -> list[dict]:
    """Recently promoted-to-canon memories (HEURISTIC PROXY).

    Definition mirrors ``digest._compute_metrics``: CANON-maturity
    memories whose ``updated_at`` falls inside the window. Memee's OSS
    schema doesn't track maturity transitions in a separate ledger
    (no ``promoted_at`` column) so we use ``updated_at`` as the closest
    available signal. False positives: a CANON memory whose tags or
    content was hand-corrected during the window. False negatives: a
    promotion older than the window. The proxy errs small.

    We surface ``promoted_at`` in the dict using ``updated_at`` so the
    consumer doesn't have to learn the proxy detail at the call site —
    this is the same lie the digest tells, encoded in one place.
    """
    from memee.storage.models import MaturityLevel, Memory

    rows = (
        session.query(Memory)
        .filter(Memory.maturity == MaturityLevel.CANON.value)
        .filter(Memory.updated_at.isnot(None))
        .filter(Memory.updated_at >= since)
        .order_by(Memory.updated_at.desc())
        .limit(RECENT_CANON_CAP)
        .all()
    )

    return [
        {
            "mem_id": m.id,
            "title": m.title or "",
            "promoted_at": (
                m.updated_at.isoformat() if m.updated_at is not None else ""
            ),
            "cite": _cite(m.id),
        }
        for m in rows
    ]


def _query_needs_review(session) -> list[dict]:
    """Hypotheses with low confidence + non-zero validation activity
    (HEURISTIC PROXY).

    Same definition as ``digest._compute_metrics``: ``maturity =
    hypothesis`` AND ``confidence_score < 0.4`` AND
    (``invalidation_count > 0 OR validation_count > 0``). The window is
    *not* applied here on purpose — needs-review is an ambient signal,
    not a recent-activity one. A hypothesis that drifted under 0.4 three
    weeks ago still needs review today; ageing it out by date would let
    the queue rot. Sorted ascending by confidence so the most-broken
    items surface first.
    """
    from sqlalchemy import or_

    from memee.storage.models import MaturityLevel, Memory

    rows = (
        session.query(Memory)
        .filter(Memory.maturity == MaturityLevel.HYPOTHESIS.value)
        .filter(Memory.confidence_score < 0.4)
        .filter(or_(Memory.invalidation_count > 0, Memory.validation_count > 0))
        .order_by(Memory.confidence_score.asc(), Memory.title)
        .limit(NEEDS_REVIEW_CAP)
        .all()
    )

    return [
        {
            "mem_id": m.id,
            "title": m.title or "",
            "confidence": float(m.confidence_score or 0.0),
            "cite": _cite(m.id),
        }
        for m in rows
    ]


def _query_impact_counts(session, since: datetime) -> dict:
    """Window-bounded counters for the headline + ROI footer.

    Mirrors the digest's ``applied_types`` and ``warning_types`` sets so
    the same numbers show up in both surfaces. Adds two pulse-only
    fields:
      * ``mistakes_prevented`` — count of MISTAKE_AVOIDED events in the
        window (deliveries, not unique memories — matches the
        agent-centric mental model used in ``engine.impact``).
      * ``time_saved_minutes`` — sum of ``time_saved_minutes`` across
        every event in the window.
    """
    from sqlalchemy import func

    from memee.engine.impact import ImpactEvent, ImpactType
    from memee.storage.models import Memory

    applied_types = (
        ImpactType.KNOWLEDGE_REUSED.value,
        ImpactType.MISTAKE_AVOIDED.value,
        ImpactType.DECISION_INFORMED.value,
    )
    warning_types = (
        ImpactType.MISTAKE_AVOIDED.value,
        ImpactType.MISTAKE_MADE.value,
        ImpactType.WARNING_INEFFECTIVE.value,
    )

    memories_applied = (
        session.query(func.count(ImpactEvent.id))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.impact_type.in_(applied_types))
        .scalar()
        or 0
    )
    warnings_checked = (
        session.query(func.count(ImpactEvent.id))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.impact_type.in_(warning_types))
        .scalar()
        or 0
    )
    mistakes_prevented = (
        session.query(func.count(ImpactEvent.id))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.impact_type == ImpactType.MISTAKE_AVOIDED.value)
        .scalar()
        or 0
    )
    time_saved = (
        session.query(func.coalesce(func.sum(ImpactEvent.time_saved_minutes), 0.0))
        .filter(ImpactEvent.created_at >= since)
        .scalar()
        or 0.0
    )

    # Investment side of the ROI ratio: ~5 minutes to author each memory
    # in the store. Same constant as ``engine.impact.get_impact_summary``
    # so the two ROI numbers stay comparable. We compute *all-time*
    # investment because the recorded memories are still active over the
    # window (a 6-month-old canon entry that prevents a mistake today
    # was still "an investment").
    total_memories = session.query(func.count(Memory.id)).scalar() or 0

    return {
        "memories_applied": int(memories_applied),
        "warnings_checked": int(warnings_checked),
        "mistakes_prevented": int(mistakes_prevented),
        "time_saved_minutes": int(round(time_saved)),
        "total_memories": int(total_memories),
    }


# ── Public API ──────────────────────────────────────────────────────────


def compute_pulse(session, days: int = 7) -> dict:
    """Build a structured pulse dict for the last ``days`` window.

    Pure data shape — no formatting. The CLI / tooling consumer picks
    its own renderer.

    Returns the same keys whether the window is empty or full (mirroring
    ``engine.impact.get_impact_summary``'s shape stability fix). On any
    error — DB lock, schema drift, integer overflow, anything — returns
    the canonical "quiet week" payload instead of raising.

    Schema::

        {
          "days":  int,                  # window size
          "since": iso8601,              # window start (UTC)
          "until": iso8601,              # window end   (UTC)
          "headline": str,               # one-liner; reuses M1 receipt
                                          # if available, else hand-rolled.
                                          # 'Memee was quiet this week.' on
                                          # zero data.
          "top_reused":     [{...}, ...],   # ≤ 3
          "top_prevented":  [{...}, ...],   # ≤ 3
          "recent_canon":   [{...}, ...],   # ≤ 5
          "needs_review":   [{...}, ...],   # ≤ 10
          "time_saved_minutes": int,
          "roi": float | None,
        }

    Each bucket dict ends with a ``cite`` key holding ``[mem:xxxxxxxx]``
    so a downstream renderer can drop it in unchanged.
    """
    # Negative / zero days don't make sense — clamp to 1 so the SQL
    # filter is at least *some* window, never the whole DB. The headline
    # still says "last 1 day" honestly.
    if not isinstance(days, int) or days <= 0:
        days = 7

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    try:
        impact_counts = _query_impact_counts(session, since)
        top_reused = _query_top_reused(session, since)
        top_prevented = _query_top_prevented(session, since)
        recent_canon = _query_recent_canon(session, since)
        needs_review = _query_needs_review(session)
    except Exception:
        # Schema drift, DB lock, missing table — collapse to quiet.
        # No cache to invalidate; ``compute_pulse`` is a pure read.
        return _quiet_payload(days, since, until)

    # Headline: try M1's formatter first, fall back to the hand-rolled
    # version. Both paths can return None; on None we use the canonical
    # "quiet week" line so the renderer's empty-state branch fires.
    headline = _try_receipt_headline(impact_counts, days)
    if not headline:
        headline = _fallback_headline(impact_counts, days)

    # ROI: time-saved divided by investment-minutes (~5 min/memory),
    # mirroring ``engine.impact.get_impact_summary``'s ratio. ``None``
    # when there's no investment side (empty store) so the renderer can
    # suppress the ROI footer cleanly instead of printing "ROI: 0.0x".
    investment_minutes = impact_counts["total_memories"] * 5
    if investment_minutes > 0:
        roi: float | None = round(
            impact_counts["time_saved_minutes"] / investment_minutes, 1
        )
    else:
        roi = None

    return {
        "days": days,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "headline": headline,
        "top_reused": top_reused,
        "top_prevented": top_prevented,
        "recent_canon": recent_canon,
        "needs_review": needs_review,
        "time_saved_minutes": impact_counts["time_saved_minutes"],
        "roi": roi,
    }


def format_pulse(payload: dict) -> str:
    """Render a pulse payload as a multi-line markdown block for the CLI.

    Shape::

        <headline>

        ## Top reuses
        - <title> — applied N× (maturity) [mem:xxxxxxxx]
        ...

        ## Mistakes prevented
        - <title> — severity=critical [mem:xxxxxxxx]
        ...

        ## Recently promoted to canon
        - <title> [mem:xxxxxxxx]
        ...

        ## Needs review
        - <title> — conf=0.32 [mem:xxxxxxxx]
        ...

        ## ROI
        N min saved · ROI Mx (proxy: 5 min/memory invested)

    Empty payload (every list zero AND no time saved) → just the
    headline. The "Needs review" section is suppressed on empty (it's
    the only ambient bucket, so showing an empty header would be noise).
    """
    if not isinstance(payload, dict):
        return "Memee was quiet this week."

    headline = (payload.get("headline") or "Memee was quiet this week.").strip()
    top_reused = payload.get("top_reused") or []
    top_prevented = payload.get("top_prevented") or []
    recent_canon = payload.get("recent_canon") or []
    needs_review = payload.get("needs_review") or []
    time_saved = int(payload.get("time_saved_minutes") or 0)
    roi = payload.get("roi")

    has_anything = bool(
        top_reused or top_prevented or recent_canon or needs_review or time_saved
    )
    if not has_anything:
        # Quiet week: just the headline. Don't render empty section
        # headers — the user already knows nothing happened.
        return headline

    lines: list[str] = [headline, ""]

    # ── Top reuses ──
    lines.append("## Top reuses")
    if top_reused:
        for item in top_reused:
            title = _truncate_title(item.get("title"))
            count = int(item.get("apply_count") or 0)
            maturity = (item.get("maturity") or "").strip() or "?"
            cite = item.get("cite") or _cite(item.get("mem_id") or "")
            applied_word = "1×" if count == 1 else f"{count}×"
            lines.append(
                f"- {title} — applied {applied_word} ({maturity}) {cite}"
            )
    else:
        lines.append("- (none)")
    lines.append("")

    # ── Mistakes prevented ──
    lines.append("## Mistakes prevented")
    if top_prevented:
        for item in top_prevented:
            title = _truncate_title(item.get("title"))
            severity = (item.get("severity") or "").strip() or "?"
            cite = item.get("cite") or _cite(item.get("mem_id") or "")
            lines.append(
                f"- {title} — severity={severity} {cite}"
            )
    else:
        lines.append("- (none)")
    lines.append("")

    # ── Recently promoted to canon ──
    lines.append("## Recently promoted to canon")
    if recent_canon:
        for item in recent_canon:
            title = _truncate_title(item.get("title"))
            cite = item.get("cite") or _cite(item.get("mem_id") or "")
            lines.append(f"- {title} {cite}")
    else:
        lines.append("- (none)")
    lines.append("")

    # ── Needs review (only if non-empty — ambient, not a "you have
    # zero issues" trophy header) ──
    if needs_review:
        lines.append("## Needs review")
        for item in needs_review:
            title = _truncate_title(item.get("title"))
            try:
                conf = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            cite = item.get("cite") or _cite(item.get("mem_id") or "")
            lines.append(
                f"- {title} — conf={conf:.2f} {cite}"
            )
        lines.append("")

    # ── ROI footer — honest about the proxy. Always rendered when there
    # is *some* activity, even if time_saved is zero, so the user can
    # see Memee's accounting (and the proxy line) without having to dig
    # into the source. ──
    lines.append("## ROI")
    saved_word = "minute" if time_saved == 1 else "minutes"
    if roi is None:
        lines.append(
            f"{time_saved} {saved_word} saved "
            "(no investment data — ROI undefined)."
        )
    else:
        lines.append(
            f"{time_saved} {saved_word} saved · ROI {roi}× "
            "(proxy: 5 min invested per memory in the store)."
        )

    # Trim a trailing blank line if the last block left one.
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)
