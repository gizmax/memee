"""Evidence Ledger: provenance chain for every memory.

Every memory should answer: WHY do we believe this?

Evidence types:
  incident   — "We learned this because X broke" (commit, ticket, postmortem)
  validation — "Agent X confirmed this in project Y"
  code_ref   — "This pattern exists in file Z, line N"
  review     — "Code review caught this in PR #123"
  test       — "Test proves this: pytest test_X.py"
  external   — "Based on docs/article/RFC at URL"

The chain grows over time. More evidence = more trust.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from memee.storage.models import Memory

# Per-memory mutexes so two concurrent ``add_evidence`` calls on the SAME
# memory serialise at the Python level. Without this, each thread would
# read the same snapshot of ``evidence_chain`` and the last commit wins,
# silently dropping evidence entries. Keyed by memory_id so calls to
# different memories still run in parallel. The dict-level lock is only
# held long enough to fetch-or-create the per-memory Lock.
_evidence_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_evidence_locks_guard = threading.Lock()


def _lock_for(memory_id: str) -> threading.Lock:
    with _evidence_locks_guard:
        return _evidence_locks[memory_id]


def add_evidence(
    session: Session,
    memory_id: str,
    evidence_type: str,
    reference: str,
    agent: str = "",
    outcome: str = "",
) -> dict:
    """Add evidence to a memory's provenance chain.

    Args:
        memory_id: Which memory
        evidence_type: incident, validation, code_ref, review, test, external
        reference: Specific ref (commit hash, PR URL, file:line, test name)
        agent: Who provided this evidence
        outcome: Result (success, failure, confirmed, disproved)

    Returns the new evidence entry.
    """
    memory = session.get(Memory, memory_id)
    if not memory:
        return {"error": f"Memory not found: {memory_id}"}

    entry = {
        "type": evidence_type,
        "ref": reference,
        "agent": agent,
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Concurrency: two threads both reading the SAME snapshot of
    # ``evidence_chain``, both appending, and the second commit
    # overwriting the first entry. Serialise per-memory at the Python
    # level and re-read the column from the DB under the lock so the
    # append always extends the committed chain.
    with _lock_for(memory_id):
        # Expire so the next attribute access reads fresh JSON from the DB,
        # not the cached copy this session loaded earlier.
        try:
            session.expire(memory, ["evidence_chain"])
        except Exception:
            # Detached / no longer in session — fall back to re-fetch.
            memory = session.get(Memory, memory_id)
            if not memory:
                return {"error": f"Memory not found: {memory_id}"}
        chain = list(memory.evidence_chain or [])
        chain.append(entry)
        memory.evidence_chain = chain
        session.commit()
    return entry


def get_evidence(session: Session, memory_id: str) -> list[dict]:
    """Get full evidence chain for a memory."""
    memory = session.get(Memory, memory_id)
    if not memory:
        return []
    return list(memory.evidence_chain or [])


def get_evidence_summary(session: Session, memory_id: str) -> dict:
    """Summarize evidence for a memory."""
    chain = get_evidence(session, memory_id)
    if not chain:
        return {"total": 0, "verdict": "no evidence"}

    by_type = {}
    for e in chain:
        t = e.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    outcomes = [e.get("outcome", "") for e in chain if e.get("outcome")]
    positive = sum(1 for o in outcomes if o in ("success", "confirmed"))
    negative = sum(1 for o in outcomes if o in ("failure", "disproved"))

    if positive > negative * 2:
        verdict = "strong evidence"
    elif positive > negative:
        verdict = "moderate evidence"
    elif negative > positive:
        verdict = "contested"
    else:
        verdict = "inconclusive"

    return {
        "total": len(chain),
        "by_type": by_type,
        "positive": positive,
        "negative": negative,
        "verdict": verdict,
        "latest": chain[-1] if chain else None,
    }
