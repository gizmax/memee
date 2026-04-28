"""Aggregate session receipt — what Memee did in this session, in one line.

The brief / Stop chain already prepends three receipts: the weekly digest,
the last-session summary, and the update notice. v2.2 adds a fourth — an
aggregate counter line that says what Memee actually accomplished in the
*current* (mid- or just-ended) session window:

    Memee reused 2 memories, prevented 1 known mistake, saved ~8 min.

That's the legacy / explicit "tool voice". The UX expert was insistent on
a contrarian: **the receipt's job is to make the user trust the AGENT,
not Memee.** A line that brags about Memee reframes the agent's work as
"the tool's win". Reframe receipts as the agent's footnote, not Memee's
brag — and the same content reads as:

    Pulling from "React Query keys must include tenant id" — settled in
    this canon last March. [mem:c12a7e8f]

So this module ships a **voice flag** (``agent | tool``), default
``agent`` for new installs. Tool voice is preserved for users who already
quote the line in their docs / dashboards and don't want it to mutate.

Design rules (mirrors digest.py + session_ledger.py)
----------------------------------------------------

* **Silence is a feature**. When ``reused == 0 AND prevented == 0`` we
  return ``None`` — without a concrete signal we don't claim time saved.
  No "Memee did nothing this session" footnote, ever.
* **Honest numbers**. ``saved_min`` is rounded to the nearest 5 minutes
  AND suppressed below 3 — a 30-second saving expressed as "saved ~0
  min" reads worse than not mentioning it at all.
* **Killable**. ``MEMEE_NO_RECEIPT=1`` (any non-empty value) → ``None``.
  Symmetrical with ``MEMEE_NO_DIGEST`` and ``MEMEE_NO_SESSION_RECEIPT``.
* **Best-effort**. Every IO / DB error collapses to ``None``. The hook
  must never break the agent's session.

Voice resolution
----------------

Explicit ``voice=`` arg wins over ``MEMEE_RECEIPT_VOICE`` env wins over
the default ``agent``. Unknown values silently fall back to ``agent`` —
we never raise on a typo'd env var because typoing it shouldn't break
the hook. Read at format-time so tests can monkeypatch the env without
re-importing the module.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional


# ── Voice resolution ───────────────────────────────────────────────────


_VALID_VOICES = ("agent", "tool")
_DEFAULT_VOICE = "agent"


def _resolve_voice(explicit: Optional[str]) -> str:
    """Pick a voice. Explicit arg wins, then env, then default.

    Unknown values silently fall back to the default — typoing
    ``MEMEE_RECEIPT_VOICE=ageny`` shouldn't make the hook louder than
    silence.
    """
    if explicit and explicit in _VALID_VOICES:
        return explicit
    env = os.environ.get("MEMEE_RECEIPT_VOICE", "").strip().lower()
    if env in _VALID_VOICES:
        return env
    return _DEFAULT_VOICE


# ── Counter aggregation ────────────────────────────────────────────────


# Impact types that count as "memory was reused" for the receipt. Mirrors
# digest.py's "applied_types" minus MISTAKE_AVOIDED — that one is its own
# counter (``prevented``) because the wording is materially different
# ("avoided a known mistake" vs "applied a known pattern").
_REUSED_TYPES = ("knowledge_reused", "decision_informed")
_PREVENTED_TYPES = ("mistake_avoided",)


def _aggregate_counters(session, since: datetime, until: datetime) -> dict:
    """Pull counters from ``impact_events`` for the window ``[since, until)``.

    Returns a dict with three integer-ish keys:

      * ``reused`` — count(KNOWLEDGE_REUSED) + count(DECISION_INFORMED)
      * ``prevented`` — count(MISTAKE_AVOIDED)
      * ``saved_min`` — sum(time_saved_minutes), rounded to nearest 5 OR
        ``0`` when the unrounded sum is < 3 (suppressed). The receipt's
        wording downstream checks ``saved_min`` against 0 to decide
        whether to mention it at all.

    Window semantics: half-open ``[since, until)`` so back-to-back
    sessions don't double-count an event whose timestamp lands on the
    boundary. ``ImpactEvent.created_at`` is the timestamp we filter on.
    """
    from sqlalchemy import func

    from memee.engine.impact import ImpactEvent

    reused = (
        session.query(func.count(ImpactEvent.id))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.created_at < until)
        .filter(ImpactEvent.impact_type.in_(_REUSED_TYPES))
        .scalar()
        or 0
    )
    prevented = (
        session.query(func.count(ImpactEvent.id))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.created_at < until)
        .filter(ImpactEvent.impact_type.in_(_PREVENTED_TYPES))
        .scalar()
        or 0
    )
    raw_saved = (
        session.query(func.coalesce(func.sum(ImpactEvent.time_saved_minutes), 0.0))
        .filter(ImpactEvent.created_at >= since)
        .filter(ImpactEvent.created_at < until)
        .scalar()
        or 0.0
    )
    raw_saved = float(raw_saved or 0.0)
    saved_min = _round_saved_minutes(raw_saved)

    return {
        "reused": int(reused),
        "prevented": int(prevented),
        "saved_min": int(saved_min),
    }


def _round_saved_minutes(raw: float) -> int:
    """Round ``raw`` minutes to the nearest 5; return 0 below 3.

    The "below 3" floor is the silence rule — a 90-second saving rounded
    to "5 min" overstates; rounded to "0 min" is just noise. Treating it
    as "no claim" is the honest move.
    """
    if raw < 3:
        return 0
    return int(round(raw / 5.0) * 5)


# ── Tool voice (legacy / explicit) ─────────────────────────────────────


def _format_tool_voice(counters: dict) -> str:
    """Render the brand-forward "Memee reused N, prevented M, saved ~K min."

    Drops zero counters; drops ``saved_min`` if it was suppressed
    (== 0). The caller has already enforced the silence rule (at least
    one of reused / prevented is non-zero), so the result is always
    non-empty here.
    """
    reused = counters["reused"]
    prevented = counters["prevented"]
    saved = counters["saved_min"]

    parts: list[str] = []
    if reused:
        parts.append(
            f"reused {reused} {'memory' if reused == 1 else 'memories'}"
        )
    if prevented:
        parts.append(
            f"prevented {prevented} known "
            f"{'mistake' if prevented == 1 else 'mistakes'}"
        )
    if saved:
        parts.append(f"saved ~{saved} min")

    return f"Memee {', '.join(parts)}."


# ── Agent voice (default) ──────────────────────────────────────────────


# Numeric weight for the maturity ladder so ``confidence × maturity``
# picks "stronger memory" over "noisier hypothesis with high confidence".
# Mirrors session_ledger._MATURITY_WEIGHT — kept inline (not imported)
# so this module stays loadable without the ledger import chain.
_MATURITY_WEIGHT = {
    "canon": 4.0,
    "validated": 3.0,
    "tested": 2.0,
    "hypothesis": 1.0,
    "deprecated": 0.0,
}


def _pick_headline_event(session, since: datetime, until: datetime):
    """Pick the most-significant impact event for the agent-voice line.

    Significance order, highest first (matches the Stop receipt's logic
    in cli._format_stop_receipt and engine.feedback.post_task_review):

      1. MISTAKE_AVOIDED  (warning surfaced AND agent dodged the bullet)
      2. KNOWLEDGE_REUSED (canon was applied — strongest pattern reuse)
      3. DECISION_INFORMED (historical context shaped a decision)

    Within a tier the winner is ``confidence_score × maturity_weight``
    descending, ties broken by most-recent ``created_at``. Returns the
    event row or ``None`` if there's nothing to surface.
    """
    from memee.engine.impact import ImpactEvent, ImpactType

    tiers = (
        ImpactType.MISTAKE_AVOIDED.value,
        ImpactType.KNOWLEDGE_REUSED.value,
        ImpactType.DECISION_INFORMED.value,
    )
    for kind in tiers:
        events = (
            session.query(ImpactEvent)
            .filter(ImpactEvent.created_at >= since)
            .filter(ImpactEvent.created_at < until)
            .filter(ImpactEvent.impact_type == kind)
            .all()
        )
        if not events:
            continue
        # Score each event by its memory's confidence × maturity weight.
        # Tied scores fall back to the most recent created_at.
        best = None
        best_score: tuple[float, datetime] | None = None
        for ev in events:
            mem = ev.memory
            if mem is None:
                continue
            try:
                conf = float(mem.confidence_score or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            weight = _MATURITY_WEIGHT.get(
                str(mem.maturity or "").lower(), 1.0
            )
            score = conf * weight
            ts = ev.created_at or datetime.min
            candidate = (score, ts)
            if best_score is None or candidate > best_score:
                best_score = candidate
                best = ev
        if best is not None:
            return best
    return None


def _truncate_title(title: str, max_chars: int) -> str:
    """Truncate a memory title to ``max_chars`` characters (one-char ellipsis).

    Matches cli._truncate_title's policy so the agent-voice line and the
    Stop receipt agree on truncation. Empty / short titles pass through.
    """
    if not title or len(title) <= max_chars:
        return title or ""
    if max_chars < 1:
        return ""
    return title[: max_chars - 1].rstrip() + "…"


def _short_hash(memory_id: str) -> str:
    """First 8 hex chars of the UUID, dashes stripped — matches the
    ``[mem:xxxxxxxx]`` convention enforced by ``engine.citations``.
    """
    if not memory_id:
        return ""
    return memory_id.replace("-", "")[:8]


def _format_when(created_at: datetime | None) -> str:
    """Render an event's "when" as a coarse, human-friendly phrase.

    Examples (relative to ``datetime.utcnow``):

      * < 7 days     → "this week"
      * < 14 days    → "last week"
      * < 60 days    → "last <Month>"  (e.g. "last March")
      * < 365 days   → "earlier this year"
      * older        → "in <YYYY>"

    Uses ``datetime.utcnow`` for the comparison anchor so the renderer
    is timezone-agnostic — events stamped at UTC compare consistently.
    A None or future ``created_at`` falls back to "recently" so a clock
    skew never produces gibberish like "in 2027".
    """
    if created_at is None:
        return "recently"
    # Strip tzinfo for a clean diff — ImpactEvent.created_at is stored
    # as a naive UTC datetime (storage.models.utcnow returns naive UTC).
    # Use ``datetime.now(timezone.utc)`` and drop the tz so we compare
    # apples-to-apples without tripping the deprecation on
    # ``datetime.utcnow()`` in Python 3.12+.
    ref = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta_days = (now - ref).days
    if delta_days < 0:
        return "recently"
    if delta_days < 7:
        return "this week"
    if delta_days < 14:
        return "last week"
    if delta_days < 60:
        return f"last {ref.strftime('%B')}"
    if delta_days < 365:
        return "earlier this year"
    return f"in {ref.year}"


def _format_agent_voice(session, since: datetime, until: datetime, counters: dict) -> str | None:
    """Render the agent's-footnote phrasing citing the headline memory.

    Picks the most-significant memory for the window (see
    ``_pick_headline_event``) and crafts one of three sentences keyed
    on the impact type:

      * MISTAKE_AVOIDED   → 'Avoided a repeat of "<title>" from past project. [mem:xxxxxxxx]'
      * KNOWLEDGE_REUSED  → 'Applied "<title>" — pattern reused N time(s) before. [mem:xxxxxxxx]'
      * DECISION_INFORMED → 'Pulling from "<title>" — settled in this canon last <when>. [mem:xxxxxxxx]'

    Returns ``None`` if no headline event could be picked (DB is dry,
    every event has a missing memory row, etc.). Caller falls back to
    the tool voice in that case so the user still sees *something*.

    Title truncated to 60 chars + '…'. Whole line capped at 140 chars
    by trimming the title further until it fits.
    """
    event = _pick_headline_event(session, since, until)
    if event is None or event.memory is None:
        return None

    mem = event.memory
    title = (mem.title or "").strip() or "(untitled)"
    truncated = _truncate_title(title, 60)
    short = _short_hash(mem.id or "")
    cite = f"[mem:{short}]" if short else "[mem:?]"

    kind = event.impact_type
    # The "before" reuse count is the memory's application_count — every
    # prior application is one "time reused before". Subtract 1 so the
    # count excludes *this* application; clamp at 0.
    try:
        prior = max(int(mem.application_count or 0) - 1, 0)
    except (TypeError, ValueError):
        prior = 0

    when = _format_when(event.created_at)

    if kind == "mistake_avoided":
        sentence = (
            f'Avoided a repeat of "{truncated}" from past project. {cite}'
        )
    elif kind == "knowledge_reused":
        sentence = (
            f'Applied "{truncated}" — pattern reused '
            f"{prior} {'time' if prior == 1 else 'times'} before. {cite}"
        )
    else:
        # DECISION_INFORMED + forward-compat fall-through. The "settled
        # in this canon last <when>" wording reads as a citation of
        # established practice, which is exactly what an informed
        # decision is.
        sentence = (
            f'Pulling from "{truncated}" — settled in this canon '
            f"{when}. {cite}"
        )

    # Hard 140-char cap. If a long title pushed us over, trim the title
    # in 8-char chunks until we fit. Worst case the title becomes just
    # the ellipsis — better than a truncated cite token.
    while len(sentence) > 140 and len(truncated) > 4:
        truncated = _truncate_title(truncated, max(len(truncated) - 8, 4))
        if kind == "mistake_avoided":
            sentence = (
                f'Avoided a repeat of "{truncated}" from past project. '
                f"{cite}"
            )
        elif kind == "knowledge_reused":
            sentence = (
                f'Applied "{truncated}" — pattern reused '
                f"{prior} {'time' if prior == 1 else 'times'} before. "
                f"{cite}"
            )
        else:
            sentence = (
                f'Pulling from "{truncated}" — settled in this canon '
                f"{when}. {cite}"
            )
    return sentence


# ── Public API ─────────────────────────────────────────────────────────


def format_session_receipt(
    session,
    *,
    since: datetime,
    until: datetime,
    voice: str | None = None,
) -> str | None:
    """Aggregate receipt for the (since, until) window. Returns one line, or
    ``None`` when there's no signal worth surfacing.

    Voice resolution: explicit ``voice`` arg → ``MEMEE_RECEIPT_VOICE`` env
    → default ``agent``. Unknown values fall back to ``agent``.

    Counters from ``impact_events`` (engine/impact.py:ImpactType):
      * ``reused`` = count(KNOWLEDGE_REUSED) + count(DECISION_INFORMED)
      * ``prevented`` = count(MISTAKE_AVOIDED)
      * ``saved_min`` = sum(time_saved_minutes), rounded to nearest 5;
        suppressed if < 3 unrounded.

    Silence rule: when ``reused == 0 AND prevented == 0`` we return
    ``None`` regardless of ``saved_min`` — without a concrete signal we
    don't claim time saved. Honours ``MEMEE_NO_RECEIPT=1`` (any non-empty
    value).

    Voice = ``tool`` (legacy / explicit):
      ``Memee reused N memories, prevented M known mistakes, saved ~K min.``
      (zero counters dropped; ``saved_min`` dropped if suppressed)

    Voice = ``agent`` (default):
      Reframes as the agent's footnote citing the most-significant
      memory by name. Picks the same memory the Stop receipt's logic
      would (most-significant warning OR strongest pattern reuse), then
      crafts one of three sentences keyed on the impact type. Title
      truncated to 60 chars + ``…``. Whole line capped at 140 chars.

    Best-effort: every error collapses to ``None``. The hook must never
    break the agent's session.
    """
    if os.environ.get("MEMEE_NO_RECEIPT"):
        return None
    try:
        counters = _aggregate_counters(session, since, until)
    except Exception:
        return None

    # Silence rule: without a concrete signal (reused or prevented),
    # don't claim time saved. ``saved_min`` is a SUPPORTING counter, not
    # a leading one.
    if counters["reused"] == 0 and counters["prevented"] == 0:
        return None

    voice_resolved = _resolve_voice(voice)

    if voice_resolved == "tool":
        try:
            return _format_tool_voice(counters)
        except Exception:
            return None

    # Agent voice (default). If the headline picker can't find a memory
    # row (e.g. legacy events with broken FKs), fall back to the tool
    # voice rather than going silent — the user still came back for the
    # signal.
    try:
        agent_line = _format_agent_voice(session, since, until, counters)
    except Exception:
        agent_line = None
    if agent_line:
        return agent_line
    try:
        return _format_tool_voice(counters)
    except Exception:
        return None
