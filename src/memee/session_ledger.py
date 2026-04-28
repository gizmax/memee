"""Session ledger — citation eventing for the next briefing.

Memee's citation footer instructs agents to cite memories they apply with
``[mem:<8-char-id>]``. Today, the round-trip is silent: the cite goes into
the evidence chain, but the user never sees Memee acknowledge it. This
module closes that loop.

How it works
------------

Two pure functions, no globals:

  * ``record_session_end(session)`` — at the end of a session (the Stop
    hook fires it via ``memee learn --auto``), snapshot every citation
    that was created since ``last_ended_at`` into
    ``~/.memee/last_session_cites.json`` and advance the marker.

  * ``format_session_summary()`` — at the start of the next session, the
    SessionStart briefing reads the snapshot and prepends a one-line
    receipt: "Last session: applied N memories. Confirmed: '<title>'
    [mem:xxxxxxxx]." Returns ``None`` when there's nothing to say so the
    caller can prepend unconditionally.

The "session" boundary is intentionally simple: there's no robust per-
session marker on the harness side, so we use "since the last
``record_session_end()`` call" — the Stop hook is the only writer, and it
fires on every Stop event. First-ever call: empty snapshot, just sets
the marker.

Citations are stored as ``evidence_chain`` entries on Memory rows with
``kind == "citation"``. We scan all memories that have been touched
recently (``last_applied_at >= last_ended_at`` is a cheap pre-filter)
and pick out evidence entries whose ``ts`` is newer than the marker.

Kill switch
-----------

``MEMEE_NO_SESSION_RECEIPT=1`` (any non-empty value) disables the
prepend. ``record_session_end`` still writes the cache so the marker
advances; only ``format_session_summary`` short-circuits to ``None``.
This keeps the behaviour symmetrical with ``MEMEE_NO_UPDATE_CHECK`` and
means turning the receipt back on after a few sessions doesn't dump a
backlog of citations into one briefing.

Best-effort
-----------

Both functions swallow every exception. The receipt is a hint, not load-
bearing. A corrupt cache, a read-only HOME, a schema drift on the
``Memory`` table — none of those should ever break the Stop hook or the
SessionStart briefing.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Cache file path. Lives next to the existing update-check cache under
# ``~/.memee/`` so a single ``rm -rf ~/.memee`` resets every Memee hint
# at once. JSON for human inspectability and stability across versions.
CACHE_PATH = Path.home() / ".memee" / "last_session_cites.json"

# Numeric weight for the maturity ladder so ``confidence × maturity``
# picks "stronger memory" over "noisier hypothesis with high confidence".
# Deprecated memories never win even on a tied confidence — they get a
# zero weight so they fall out of the ranking entirely.
_MATURITY_WEIGHT = {
    "canon": 4.0,
    "validated": 3.0,
    "tested": 2.0,
    "hypothesis": 1.0,
    "deprecated": 0.0,
}


# ── Public API ─────────────────────────────────────────────────────────


def record_session_end(session) -> None:
    """Snapshot citations created since ``last_ended_at`` and advance the marker.

    Writes ``~/.memee/last_session_cites.json`` with shape::

        {
          "ended_at": "<iso8601>",
          "citations": [
            {"mem_id": "...", "title": "...", "resolved_at": "<iso>",
             "confidence": 0.87, "maturity": "canon"},
            ...
          ]
        }

    First-ever call (no cache file): empty snapshot, just stamps
    ``ended_at`` so the next call has a real lower bound. Best-effort:
    every error is swallowed — this is a hint, not load-bearing.
    """
    try:
        previous = _read_cache()
        last_ended_at = _parse_iso(
            previous.get("ended_at") if isinstance(previous, dict) else None
        )
        now = datetime.now(timezone.utc)
        citations: list[dict] = []
        if last_ended_at is not None:
            citations = _collect_citations_since(session, last_ended_at)
        _write_cache(
            {
                "ended_at": now.isoformat(),
                "citations": citations,
            }
        )
    except Exception:
        # Hook safety: a broken ledger must never fail the Stop hook.
        return


def format_session_summary() -> str | None:
    """Render a one-line markdown notice for the next briefing, or ``None``.

    Returns ``None`` when:
      * the cache file does not exist (first session ever),
      * the cache is corrupt or unreadable,
      * the snapshot has zero citations,
      * ``MEMEE_NO_SESSION_RECEIPT`` is set to any non-empty value.

    Otherwise returns a single line such as::

        > Last session: applied 3 memories. Confirmed: 'Never use eval()
        on user input' [mem:a81f2c9].

    The "Confirmed" memory is picked by ``confidence × maturity_weight``
    descending, ties broken by most-recent ``resolved_at``. The intent
    is to surface the strongest piece of organisational knowledge the
    agent actually applied — so the user sees Memee earn its keep.
    """
    if os.environ.get("MEMEE_NO_SESSION_RECEIPT"):
        return None
    try:
        cache = _read_cache()
    except Exception:
        return None
    if not isinstance(cache, dict):
        return None
    citations = cache.get("citations")
    if not isinstance(citations, list) or not citations:
        return None

    pick = _pick_highlight(citations)
    if pick is None:
        return None

    n = len(citations)
    title = (pick.get("title") or "").strip() or "(untitled)"
    short = _short_hash(pick.get("mem_id") or "")
    cite_token = f"[mem:{short}]" if short else "[mem:?]"

    if n == 1:
        return f"> Last session: applied 1 memory: '{title}' {cite_token}."
    return (
        f"> Last session: applied {n} memories. "
        f"Confirmed: '{title}' {cite_token}."
    )


# ── Internals ──────────────────────────────────────────────────────────


def _read_cache() -> dict | None:
    """Read the cache file, return ``None`` on absence / corruption.

    Raises only on truly exceptional conditions (the open() itself
    failing for a non-FileNotFoundError reason). Callers wrap.
    """
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_cache(payload: dict) -> None:
    """Best-effort cache write — never raise."""
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError:
        return


def _parse_iso(value) -> datetime | None:
    """Tolerant ISO8601 → aware UTC datetime. Returns ``None`` on garbage."""
    if not isinstance(value, str) or not value:
        return None
    try:
        # ``fromisoformat`` accepts what we write; tolerate a trailing Z
        # in case some upstream tool stamps it that way.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _collect_citations_since(session, since: datetime) -> list[dict]:
    """Scan memories for citation evidence newer than ``since``.

    We pre-filter on ``last_applied_at >= since`` because a citation
    always bumps that column (see ``citations.confirm_citation``). On a
    legacy DB where the column hasn't been updated for some rows the
    pre-filter is a hint, not a guarantee — we still re-check each
    evidence entry's own ``ts``. Best-effort throughout: missing fields,
    naive timestamps, even a non-list ``evidence_chain`` are silently
    skipped rather than raising.
    """
    # Lazy import: keeps this module importable in environments where the
    # SQLAlchemy stack hasn't been initialised yet (CLI ``--help`` paths,
    # tests that monkeypatch CACHE_PATH without a DB).
    from memee.storage.models import Memory

    out: list[dict] = []
    try:
        rows = (
            session.query(Memory)
            .filter(
                (Memory.last_applied_at == None)  # noqa: E711 — SQLA needs ==
                | (Memory.last_applied_at >= since)
            )
            .all()
        )
    except Exception:
        # Fall back to a full scan if the indexed predicate trips on a
        # legacy schema. Still bounded by the per-row evidence check.
        try:
            rows = session.query(Memory).all()
        except Exception:
            return []

    for m in rows:
        chain = m.evidence_chain or []
        if not isinstance(chain, list):
            continue
        for entry in chain:
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") != "citation":
                continue
            ts = _parse_iso(entry.get("ts"))
            if ts is None or ts <= since:
                continue
            out.append(
                {
                    "mem_id": m.id,
                    "title": m.title or "",
                    "resolved_at": entry.get("ts") or ts.isoformat(),
                    "confidence": float(m.confidence_score or 0.0),
                    "maturity": str(m.maturity or "hypothesis"),
                }
            )
    return out


def _pick_highlight(citations: list[dict]) -> dict | None:
    """Pick the most "load-bearing" citation to feature in the receipt.

    Score: ``confidence_score × maturity_weight``. Ties broken by the
    most-recent ``resolved_at`` so when two canon entries with identical
    confidence both fired in the session, the one cited later (likely
    the one the user remembers) wins. Returns ``None`` on an empty or
    malformed list.
    """
    if not citations:
        return None
    best: dict | None = None
    best_score: tuple[float, str] | None = None
    for c in citations:
        if not isinstance(c, dict):
            continue
        try:
            conf = float(c.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        weight = _MATURITY_WEIGHT.get(str(c.get("maturity") or ""), 1.0)
        score = conf * weight
        # Resolve_at is a string; lexicographic compare on ISO8601 is
        # chronological, which is exactly what we want for the tiebreak.
        resolved = str(c.get("resolved_at") or "")
        candidate = (score, resolved)
        if best_score is None or candidate > best_score:
            best_score = candidate
            best = c
    return best


def _short_hash(memory_id: str) -> str:
    """First 8 hex chars of the UUID, dashes stripped — matches the
    ``[mem:xxxxxxxx]`` convention enforced by ``engine.citations``.
    Kept inline (not imported) so this module stays loadable without the
    full engine import chain.
    """
    if not memory_id:
        return ""
    return memory_id.replace("-", "")[:8]
