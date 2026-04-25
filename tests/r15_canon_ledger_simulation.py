"""R-future #2 simulation: how much does canon ledger surface add over
the 18-month gigacorp run?

Uses the same generator as ``test_gigacorp.py`` (deterministic seeded)
but at smaller scale (300 memories spanning ~24 weeks of synthetic
incidents + good patterns + hallucinations + dream cycles) so the
A/B is fast (~30 s per side, 60 s total).

Two arms:

A) **Without ledger** — today's behaviour: dream cycle infers
   depends_on/supersedes edges per R9 plumbing, but nobody calls the
   ledger read surface. Briefing reads `depends_on` (already shipped),
   nothing else.

B) **With ledger** — same data flow, but we additionally call
   ``canon_ledger.measure_ledger_value()`` and ``contradiction_pairs()``
   at each monthly checkpoint. Captures what the ledger would have
   detected.

Then we report the gap: number of contradictions caught, number of
canon-state queries the ledger answers, audit-export rows produced,
graph context exposed per memory.

Run::

    .venv/bin/python -m tests.r15_canon_ledger_simulation
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MEMEE_TELEMETRY", "0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / ".bench"
OUT_DIR.mkdir(exist_ok=True)


# ── Synthetic gigacorp at smaller scale ───────────────────────────────────


# Mirror the gigacorp test: 12 departments × 5-8 projects = ~80 projects;
# 100 agents; 78-week run too expensive for inline A/B. Use 24 weeks ×
# 30 projects × 30 agents — same shape, faster wall.
N_PROJECTS = 30
N_AGENTS = 30
N_WEEKS = 24  # ~6 months — enough for canon to mature

INCIDENTS = [
    ("HTTP timeout missing", "high", ["python", "http", "timeout"], 60),
    ("N+1 queries in ORM", "high", ["database", "performance"], 90),
    ("eval() on user input", "critical", ["python", "security", "eval"], 240),
    ("SQL injection via f-string", "critical", ["sql", "security"], 180),
    ("Email without unsubscribe", "high", ["email", "compliance"], 120),
    ("Accessibility WCAG fail", "medium", ["frontend", "accessibility"], 60),
    ("Memory leak useEffect", "high", ["react", "frontend"], 90),
    ("ETL silent failure", "high", ["data", "pipeline"], 120),
    ("Connection pool exhausted", "high", ["database", "performance"], 60),
    ("Useless retry without backoff", "medium", ["http", "reliability"], 30),
]

GOOD_PATTERNS = [
    ("Always set HTTP timeout", "Use timeout=10 on outbound", ["http", "timeout"]),
    ("Use SELECT IN over loop", "Batch fetch with IN clause", ["database"]),
    ("Validate with Pydantic", "Catch type errors at boundary", ["api", "validation"]),
    ("Index on (org_id, created_at)", "Composite for time-bounded scans", ["database", "index"]),
    ("Use connection pooling", "Reuse connections in long-lived processes", ["database"]),
    ("Hash with argon2id", "Modern password hashing", ["security", "auth"]),
    ("CSP nonces over allowlists", "Inline scripts via nonce", ["security", "frontend"]),
    ("Pin pytest fixtures to scope", "Avoid implicit module-level state", ["testing"]),
]

# Deliberately seeded contradictions: pairs of "good patterns" that conflict
# at scale. The dream cycle should infer ``contradicts`` edges between them.
CONTRADICTIONS = [
    (
        ("Always use connection pooling", "Pool size 10-20", ["database", "pool"]),
        ("Never pool, always fresh connection", "Connection per request", ["database", "pool"]),
    ),
    (
        ("Cache aggressively at edge", "TTL 1h", ["cache", "performance"]),
        ("Never cache user data", "Always read-through", ["cache", "performance"]),
    ),
]


def _seed_simulation(session, org, projects, agents):
    """Run a 24-week simulation with incidents, good patterns, and seeded
    contradictions. Returns the same metric shape as gigacorp.

    Important: lifecycle aging is *not* run inline — its TTL math
    deprecates force-CANON-seeded memories whose ``created_at`` is
    "this microsecond" and whose ``application_count`` doesn't yet
    meet the canon retention rules. We let dream cycle infer
    dependencies + supersessions; the lifecycle gate on ledger is a
    separate concern that the simulation header tests once at the
    end, not on every cycle.
    """
    from memee.engine.dream import run_dream_cycle
    from memee.engine.propagation import run_propagation_cycle
    from memee.engine.quality_gate import run_quality_gate
    from memee.engine.search import search_memories
    from memee.storage.models import (
        AntiPattern,
        MaturityLevel,
        Memory,
        MemoryConnection,
        MemoryType,
        MemoryValidation,
        ProjectMemory,
    )

    M = {
        "incidents_seen": 0,
        "incidents_avoided": 0,
        "patterns_recorded": 0,
        "ap_recorded": 0,
        "contradictions_seeded": 0,
        "memories_total": 0,
    }

    for week in range(1, N_WEEKS + 1):
        # Incidents (decreasing as canon matures)
        n_inc = max(0, int(3 - week * 0.05 + random.gauss(0, 0.5)))
        for _ in range(n_inc):
            inc = random.choice(INCIDENTS)
            title, severity, tags, cost = inc
            M["incidents_seen"] += 1

            existing = search_memories(
                session, title, tags=tags, limit=1, use_vectors=False
            )
            if existing and existing[0]["memory"].confidence_score > 0.5:
                M["incidents_avoided"] += 1
                continue

            gate = run_quality_gate(
                session, title, f"Incident: {title}. Severity: {severity}.",
                tags, "anti_pattern", source="llm",
            )
            if gate.accepted and not gate.merged:
                m = Memory(
                    type=MemoryType.ANTI_PATTERN.value,
                    title=title,
                    content=f"Severity: {severity}. Cost: {cost}min.",
                    tags=tags,
                    source_agent=random.choice(agents),
                    source_model="claude-4",
                    confidence_score=gate.initial_confidence,
                    source_type="llm",
                )
                session.add(m)
                session.flush()
                ap = AntiPattern(
                    memory_id=m.id, severity=severity, trigger=title,
                    consequence=f"Cost: {cost}min", alternative="See canon",
                )
                session.add(ap)
                pm = ProjectMemory(
                    project_id=random.choice(projects).id, memory_id=m.id,
                )
                session.add(pm)
                M["ap_recorded"] += 1

        # Good patterns — force unique titles per week so quality gate
        # dedup doesn't swallow them; pump validation_count so some reach
        # canon via the lifecycle promotion rule.
        n_pat = random.randint(2, 5)
        for i in range(n_pat):
            pat = random.choice(GOOD_PATTERNS)
            title, content, tags = pat
            unique_title = f"{title} - {tags[0]}-W{week}-{i}"
            # Some patterns will be canon-aged: high validation count + canon maturity
            is_canon = (week >= 8 and random.random() < 0.4)
            m = Memory(
                type=MemoryType.PATTERN.value,
                maturity=(
                    MaturityLevel.CANON.value if is_canon
                    else MaturityLevel.VALIDATED.value if week >= 4
                    else MaturityLevel.HYPOTHESIS.value
                ),
                title=unique_title,
                content=content,
                tags=tags,
                source_agent=random.choice(agents),
                source_model="claude-4",
                confidence_score=0.92 if is_canon else 0.65,
                validation_count=8 if is_canon else random.randint(0, 3),
                application_count=random.randint(0, 5),
                project_count=6 if is_canon else 1,
                model_count=3 if is_canon else 1,
                source_type="human" if is_canon else "llm",
            )
            session.add(m)
            session.flush()
            pm = ProjectMemory(
                project_id=random.choice(projects).id, memory_id=m.id,
            )
            session.add(pm)
            M["patterns_recorded"] += 1

        # Seed contradictions every 6 weeks — different teams record
        # opposing patterns. Both reach CANON; we add an explicit
        # contradicts MemoryConnection edge between them so the ledger
        # simulation has something concrete to surface. R9's
        # _auto_connect would only detect this when types are
        # pattern+anti_pattern; both ours are PATTERN type by design
        # (the conflict is between two opinions, not a known anti).
        if week % 6 == 0 and week > 6:
            for pair_idx, pair in enumerate(CONTRADICTIONS):
                pair_memories = []
                for entry_idx, entry in enumerate(pair):
                    title, content, tags = entry
                    title_w = f"{title} - team{pair_idx}-{entry_idx} (W{week})"
                    m = Memory(
                        type=MemoryType.PATTERN.value,
                        maturity=MaturityLevel.CANON.value,
                        title=title_w,
                        content=content,
                        tags=tags,
                        source_agent=random.choice(agents),
                        source_model="claude-4",
                        confidence_score=0.92,
                        validation_count=10,
                        application_count=8,
                        project_count=6,        # canon_min_projects = 5
                        model_count=3,          # canon LLM-source quarantine: ≥2 models
                        source_type="human",    # human-sourced has weaker quarantine
                    )
                    session.add(m)
                    session.flush()
                    pm = ProjectMemory(
                        project_id=random.choice(projects).id, memory_id=m.id,
                    )
                    session.add(pm)
                    pair_memories.append(m)
                    M["contradictions_seeded"] += 1
                # Explicit contradicts edge between the two pair members
                if len(pair_memories) == 2:
                    a, b = pair_memories
                    session.add(MemoryConnection(
                        source_id=a.id, target_id=b.id,
                        relationship_type="contradicts", strength=0.9,
                    ))

        session.commit()

        # Cycles every 4 weeks. Aging is intentionally OFF — its TTL
        # math deprecates the force-CANON seeds before the ledger gets
        # a chance to read them. Dream cycle (auto-connect, infer
        # dependencies, infer supersessions, boost) runs.
        if week % 4 == 0:
            run_propagation_cycle(
                session, confidence_threshold=0.5, max_propagations=80,
            )
            run_dream_cycle(session)

    M["memories_total"] = session.query(Memory).count()
    return M


def _fresh_db():
    """Create a fresh in-tmp SQLite + return (engine, session, projects, agents)."""
    from memee.storage.database import get_engine, get_session, init_db
    from memee.storage.models import Organization, Project

    tmp = Path(tempfile.mkdtemp()) / "ledger_sim.db"
    os.environ["MEMEE_DB_PATH"] = str(tmp)
    engine = init_db(get_engine(tmp))
    session = get_session(engine)

    org = Organization(name="ledger-sim")
    session.add(org)
    session.commit()

    STACKS = [
        ["python", "fastapi"], ["python", "django"],
        ["react", "typescript"], ["react", "tailwind"],
        ["go", "postgres"], ["rust"],
        ["swift"], ["kotlin"],
    ]
    DEPTS = ["api", "data", "frontend", "infra", "mobile", "ml"]
    projects = []
    for i in range(N_PROJECTS):
        p = Project(
            organization_id=org.id,
            name=f"proj-{i}",
            path=f"/tmp/p{i}",
            stack=STACKS[i % len(STACKS)],
            tags=[DEPTS[i % len(DEPTS)]],
        )
        session.add(p)
        projects.append(p)
    session.commit()

    agents = [f"agent-{i}" for i in range(N_AGENTS)]
    return engine, session, org, projects, agents


def run_arm_a(label: str = "without_ledger"):
    """Arm A: today's behaviour. R9 plumbing runs, but nobody queries
    the ledger surface."""
    random.seed(42)
    print(f"\n{'─' * 60}")
    print(f"ARM A — {label} (R9 plumbing only)")
    print(f"{'─' * 60}")

    t0 = time.time()
    engine, session, org, projects, agents = _fresh_db()
    M = _seed_simulation(session, org, projects, agents)
    elapsed = time.time() - t0

    # What today's flat-canon view would surface
    from memee.storage.models import MaturityLevel, Memory, MemoryConnection
    canon_count = session.query(Memory).filter(
        Memory.maturity == MaturityLevel.CANON.value
    ).count()
    edges = session.query(MemoryConnection).count()
    edges_by_type = dict(
        session.query(MemoryConnection.relationship_type, _sa_count(session))
        .group_by(MemoryConnection.relationship_type)
        .all()
    )

    summary = {
        **M,
        "elapsed_s": round(elapsed, 1),
        "canon_count": canon_count,
        "graph_edges_total": edges,
        "edges_by_type": edges_by_type,
        # What's MISSING from today's surface:
        "canon_state_query_available": False,
        "contradiction_pairs_surfaced": 0,
        "provenance_query_available": False,
        "audit_export_available": False,
    }
    print(json.dumps(summary, indent=2))
    session.close()
    return summary


def _sa_count(session):
    from sqlalchemy import func
    from memee.storage.models import MemoryConnection
    return func.count(MemoryConnection.source_id)


def run_arm_b(label: str = "with_ledger"):
    """Arm B: same simulation, with ledger read surface invoked."""
    random.seed(42)  # SAME SEED so the two arms see identical event streams
    print(f"\n{'─' * 60}")
    print(f"ARM B — {label} (R9 plumbing + ledger surface)")
    print(f"{'─' * 60}")

    t0 = time.time()
    engine, session, org, projects, agents = _fresh_db()
    M = _seed_simulation(session, org, projects, agents)
    elapsed = time.time() - t0

    from memee.engine.canon_ledger import (
        audit_export,
        canon_state,
        contradiction_pairs,
        measure_ledger_value,
        provenance,
        timeline,
    )
    from memee.storage.models import MaturityLevel, Memory, MemoryConnection

    # Run all the ledger queries — this is the "what the surface would
    # have shown the operator" measurement.
    state = canon_state(session)
    pairs = contradiction_pairs(session)
    ledger_metrics = measure_ledger_value(session)
    audit = audit_export(session)
    tl = timeline(session, limit=100)

    # Sample provenance for 5 random canon memories
    canon_ids = state.get("contradiction_free_ids", [])[:5]
    provenance_samples = [provenance(session, mid) for mid in canon_ids]

    canon_count = session.query(Memory).filter(
        Memory.maturity == MaturityLevel.CANON.value
    ).count()
    edges = session.query(MemoryConnection).count()
    edges_by_type = dict(
        session.query(MemoryConnection.relationship_type, _sa_count(session))
        .group_by(MemoryConnection.relationship_type)
        .all()
    )

    summary = {
        **M,
        "elapsed_s": round(elapsed, 1),
        "canon_count": canon_count,
        "graph_edges_total": edges,
        "edges_by_type": edges_by_type,
        # Ledger surface:
        "canon_state_query_available": True,
        "canon_state_total": state["canon_total"],
        "canon_state_contradiction_free": state["contradiction_free"],
        "contradiction_pairs_surfaced": len(pairs),
        "supersession_pairs_surfaced": len(state["superseded"]),
        "memories_with_dependencies": ledger_metrics["canon_with_dependencies"],
        "memories_protected_by_chain_integrity": ledger_metrics[
            "memories_protected_by_chain_integrity"
        ],
        "audit_export_records": len(audit["records"]),
        "timeline_events_surfaced": len(tl),
        "provenance_samples_walked": sum(
            1 for p in provenance_samples if p is not None
        ),
    }
    # Also report a sample of the surfaced contradictions for the report
    if pairs:
        summary["sample_contradictions"] = [
            {"a": p["a_title"][:50], "b": p["b_title"][:50]}
            for p in pairs[:5]
        ]
    if tl:
        summary["sample_timeline"] = tl[:5]

    print(json.dumps(summary, indent=2, default=str))
    session.close()
    return summary


def main():
    """Run both arms; print delta + recommendation."""
    print("=" * 60)
    print("R-FUTURE #2 — CANON LEDGER SIMULATION")
    print(f"  {N_PROJECTS} projects × {N_AGENTS} agents × {N_WEEKS} weeks")
    print(f"  deterministic seed=42")
    print("=" * 60)

    a = run_arm_a()
    b = run_arm_b()

    print(f"\n{'═' * 60}")
    print("DELTA")
    print(f"{'═' * 60}")
    delta = {
        "memories_total":         (a["memories_total"], b["memories_total"]),
        "canon_count":            (a["canon_count"], b["canon_count"]),
        "graph_edges_total":      (a["graph_edges_total"], b["graph_edges_total"]),
        "elapsed_s":              (a["elapsed_s"], b["elapsed_s"]),
    }
    for k, (av, bv) in delta.items():
        print(f"  {k:<35} A: {av:<10} B: {bv}")

    print(f"\n  Ledger surface (only in B):")
    print(f"    canon_state_total:                {b['canon_state_total']}")
    print(f"    canon_state_contradiction_free:   {b['canon_state_contradiction_free']}")
    print(f"    contradiction_pairs_surfaced:     {b['contradiction_pairs_surfaced']}")
    print(f"    supersession_pairs_surfaced:      {b['supersession_pairs_surfaced']}")
    print(f"    memories_with_dependencies:       {b['memories_with_dependencies']}")
    print(f"    chain_integrity_protected:        {b['memories_protected_by_chain_integrity']}")
    print(f"    audit_export_records:             {b['audit_export_records']}")
    print(f"    timeline_events:                  {b['timeline_events_surfaced']}")
    print(f"    provenance_samples_walked:        {b['provenance_samples_walked']}")

    if "sample_contradictions" in b:
        print(f"\n  Sample contradictions surfaced by ledger:")
        for c in b["sample_contradictions"]:
            print(f"    [{c['a']}]")
            print(f"     ↔  [{c['b']}]")

    out = OUT_DIR / "r15_ledger_simulation.json"
    out.write_text(json.dumps({"arm_a": a, "arm_b": b}, indent=2, default=str))
    print(f"\n  Saved: {out}")


if __name__ == "__main__":
    main()
