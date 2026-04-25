"""Before/after autoresearch benchmark — measures correctness and perf on the
exact scenarios flagged in the P0/P1 issue list.

Run:
    python -m tests.bench_autoresearch before
    # ... apply fixes ...
    python -m tests.bench_autoresearch after
    python -m tests.bench_autoresearch compare

Each row is {scenario, metric, direction, value}. "direction" is "higher" or
"lower" (what "better" means for that metric). Correctness metrics are binary
(1=correct, 0=broken) with direction=higher.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
os.environ.setdefault("MEMEE_TELEMETRY", "0")  # benchmarks disable telemetry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / ".bench"
OUT_DIR.mkdir(exist_ok=True)


def _fresh_db():
    """Create a fresh in-tmp SQLite, init schema, return (engine, session)."""
    from memee.storage.database import get_engine, get_session, init_db
    from memee.storage.models import Organization

    tmp = Path(tempfile.mkdtemp()) / "bench.db"
    os.environ["MEMEE_DB_PATH"] = str(tmp)
    engine = init_db(get_engine(tmp))
    session = get_session(engine)
    org = Organization(name="bench-org")
    session.add(org)
    session.commit()
    return engine, session, org


def _seed(session, org, n_patterns=40, n_anti=5):
    """Seed a deterministic corpus for filter-correctness + perf checks."""
    from memee.storage.models import AntiPattern, Memory, MaturityLevel, MemoryType

    # 40 "pattern" memories tagged for testing/db/perf
    for i in range(n_patterns):
        mem = Memory(
            type=MemoryType.PATTERN.value,
            maturity=MaturityLevel.VALIDATED.value,
            title=f"pattern {i} for FastAPI testing {i}",
            content=f"Use pytest fixtures properly. Index {i}. Context matters.",
            tags=["python", "testing", "pytest", "fastapi"],
            confidence_score=0.7,
        )
        session.add(mem)

    # 5 anti-patterns sharing the same tag space — filter must find these
    for i in range(n_anti):
        mem = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            maturity=MaturityLevel.VALIDATED.value,
            title=f"anti-pattern {i} never mock DB in FastAPI testing",
            content=f"Reason: mock/prod divergence {i}. Avoid. Do NOT mock. Testing.",
            tags=["python", "testing", "pytest", "fastapi"],
            confidence_score=0.8,
        )
        session.add(mem)
        session.flush()
        ap = AntiPattern(
            memory_id=mem.id,
            severity="high",
            trigger=f"mocking DB in tests {i}",
            consequence="migration breakage",
            alternative="integration tests against real SQLite",
        )
        session.add(ap)
    session.commit()


# ── P0: Telemetry must not touch caller session on its error path ──
def bench_telemetry_caller_safety():
    """Directly exercise the telemetry exception branch. If the error handler
    still calls session.rollback() on the caller session, a pending write will
    be lost."""
    from memee.engine import telemetry as tele
    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    os.environ["MEMEE_TELEMETRY"] = "1"

    # Caller adds a row but does not commit yet
    session.add(
        Memory(
            type=MemoryType.PATTERN.value,
            maturity=MaturityLevel.VALIDATED.value,
            title="pending caller write",
            content="must survive telemetry failure",
            tags=["canary"],
            confidence_score=0.6,
        )
    )
    session.flush()

    # Count rollbacks on the caller session
    rollbacks = {"n": 0}
    orig_rb = session.rollback

    def counting_rollback(*a, **kw):
        rollbacks["n"] += 1
        return orig_rb(*a, **kw)

    session.rollback = counting_rollback

    # Force telemetry's inner try to raise by breaking get_bind — this is
    # inside record_search_event's try/except, which currently calls
    # session.rollback() on the caller session.
    orig_gb = session.get_bind

    def broken_get_bind(*a, **kw):
        raise RuntimeError("synthetic bind failure")

    session.get_bind = broken_get_bind
    try:
        tele.record_search_event(session, "anything", [], 1.0)
    finally:
        session.get_bind = orig_gb
        session.rollback = orig_rb
        os.environ["MEMEE_TELEMETRY"] = "0"

    # A rollback here is the bug — caller's pending work would be lost
    touched_caller = rollbacks["n"] > 0

    # Also verify pending write survived (end-to-end proof)
    session.commit()
    survived = (
        session.query(Memory)
        .filter(Memory.title == "pending caller write")
        .count()
        == 1
    )
    session.close()

    return {
        "caller_rollbacks": rollbacks["n"],
        "pending_write_survived": survived,
        "correct": 1.0 if (not touched_caller and survived) else 0.0,
        "direction": "higher",
    }


# ── P1: Filtered search correctness ──
def bench_filtered_search():
    """1000 patterns + 2 anti-patterns all matching the query. Search with
    memory_type='anti_pattern', limit=5. The BM25 pre-filter over-fetches
    limit*3=15 candidates without memory_type; the 2 anti-patterns may not
    beat 15 of the 1000 patterns → filter returns < 2."""
    from memee.engine.search import search_memories
    from memee.storage.models import AntiPattern, Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    for i in range(1000):
        session.add(
            Memory(
                type=MemoryType.PATTERN.value,
                maturity=MaturityLevel.VALIDATED.value,
                title=f"testing pattern {i} about pytest fixtures",
                content=f"testing is great {i}",
                tags=["testing", "python"],
                confidence_score=0.6,
            )
        )
        if i % 200 == 0:
            session.flush()
    for i in range(2):
        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            maturity=MaturityLevel.VALIDATED.value,
            title=f"anti testing {i} never do X",
            content=f"testing with the wrong fixture {i}",
            tags=["testing", "python"],
            confidence_score=0.9,
        )
        session.add(m)
        session.flush()
        session.add(
            AntiPattern(
                memory_id=m.id,
                severity="high",
                trigger="wrong fixture",
                consequence="false pass",
                alternative="use tmp_path",
            )
        )
    session.commit()

    results = search_memories(
        session,
        "testing pytest fixtures",
        memory_type="anti_pattern",
        limit=5,
        use_vectors=False,
    )
    got = len(results)
    expected = 2
    session.close()
    return {
        "hits": got,
        "expected": expected,
        "correct": 1.0 if got >= expected else got / expected,
        "direction": "higher",
    }


def bench_filtered_search_maturity():
    """1000 validated patterns + 3 hypothesis patterns, same query. Filter by
    maturity='hypothesis'. The pre-filter bug swallows the hypothesis rows."""
    from memee.engine.search import search_memories
    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    for i in range(1000):
        session.add(
            Memory(
                type=MemoryType.PATTERN.value,
                maturity=MaturityLevel.VALIDATED.value,
                title=f"validated pattern {i} about testing pytest",
                content=f"tested approach {i}",
                tags=["testing"],
                confidence_score=0.7,
            )
        )
        if i % 200 == 0:
            session.flush()
    for i in range(3):
        session.add(
            Memory(
                type=MemoryType.PATTERN.value,
                maturity=MaturityLevel.HYPOTHESIS.value,
                title=f"hypothesis row for testing pytest {i}",
                content=f"Fresh idea {i} about testing pytest",
                tags=["testing"],
                confidence_score=0.4,
            )
        )
    session.commit()

    results = search_memories(
        session,
        "testing pytest",
        maturity="hypothesis",
        limit=5,
        use_vectors=False,
    )
    got = len(results)
    expected = 3
    session.close()
    return {
        "hits": got,
        "expected": expected,
        "correct": 1.0 if got >= expected else got / expected,
        "direction": "higher",
    }


# ── P1: Router query expansion — substring false positive ──
def bench_router_expansion_precision():
    """'pricing page copy' must NOT add CI/hook/lint expansions (old code
    substring-matched 'ci' inside 'pricing'). Check only the expansion part,
    not the literal input, because 'ci' naturally appears in 'pricing'."""
    from memee.engine.router import _expand_query

    task = "pricing page copy"
    expanded = _expand_query(task)
    # Strip the original task so we only inspect what the expander added.
    added = expanded[len(task):].lower().strip()
    bad_terms = ["pre-commit", "lint", "hooks", "husky", "github actions", "pipeline"]
    leaked = [t for t in bad_terms if t in added]

    # Real CI expansion still fires when 'ci' is a real token.
    expanded_ci = _expand_query("set up CI pipeline")
    added_ci = expanded_ci[len("set up CI pipeline"):].lower()
    real_expansions = any(t in added_ci for t in ["pre-commit", "lint", "hooks"])

    return {
        "leaked_terms": leaked,
        "added_expansions": added,
        "real_expansion_intact": real_expansions,
        "correct": 1.0 if not leaked and real_expansions else 0.0,
        "direction": "higher",
    }


# ── P1: Review tag-overlap false positive rate ──
def bench_review_false_positive():
    """Seed 10 anti-patterns with varied tags; scan a diff that only matches
    ONE anti-pattern by content. Count false positives — memories warned about
    whose tag overlap is incidental (e.g. 'python' tag)."""
    from memee.engine.review import review_diff
    from memee.storage.models import AntiPattern, Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    target = Memory(
        type=MemoryType.ANTI_PATTERN.value,
        maturity=MaturityLevel.VALIDATED.value,
        title="never use requests without timeout",
        content="Unbounded requests.get hangs the worker thread forever.",
        tags=["http", "timeout"],
        confidence_score=0.9,
    )
    session.add(target)
    session.flush()
    session.add(
        AntiPattern(
            memory_id=target.id,
            severity="high",
            trigger="requests.get without timeout",
            consequence="thread hang",
            alternative="timeout=10",
        )
    )

    # 9 unrelated anti-patterns that all share the "http" keyword/tag.
    # Review's tag-overlap _check_anti_patterns will fire all of them because
    # the diff triggers the "http" keyword even though the content is unrelated.
    for i in range(9):
        m = Memory(
            type=MemoryType.ANTI_PATTERN.value,
            maturity=MaturityLevel.VALIDATED.value,
            title=f"http antipattern {i} totally unrelated topic",
            content=f"Unrelated topic {i} — cache headers, CORS, etc.",
            tags=["http", f"topic{i}"],
            confidence_score=0.7,
        )
        session.add(m)
        session.flush()
        session.add(
            AntiPattern(
                memory_id=m.id,
                severity="low",
                trigger=f"some other trigger {i}",
                consequence="slow",
                alternative="do the right thing",
            )
        )
    session.commit()

    diff = """diff --git a/foo.py b/foo.py
+import requests
+def fetch():
+    return requests.get('https://example.com').json()
"""
    result = review_diff(session, diff)
    warnings = result.get("warnings", [])
    session.close()

    # Only the timeout antipattern is *truly* relevant. Count false positives.
    true_positive = any(
        "timeout" in w.get("title", "").lower() for w in warnings
    )
    false_positives = sum(
        1 for w in warnings if "timeout" not in w.get("title", "").lower()
    )
    return {
        "total_warnings": len(warnings),
        "true_positives": 1 if true_positive else 0,
        "false_positives": false_positives,
        "fp_rate": false_positives / max(len(warnings), 1),
        "direction": "lower",
        "correct": 1.0 if true_positive and false_positives <= 2 else 0.0,
    }


# ── P1: /projects N+1 query count ──
def bench_projects_n1():
    """Count SQL queries executed for GET /projects over 50 projects.
    Target: ≤ 3 queries total (projects + memory counts in single group_by)."""
    from sqlalchemy import event

    from memee.storage.models import Memory, MemoryType, MaturityLevel, Project, ProjectMemory

    engine, session, org = _fresh_db()
    # 50 projects, each with 3 memories
    for i in range(50):
        p = Project(organization_id=org.id, name=f"proj-{i}", path=f"/tmp/p{i}", stack=["py"])
        session.add(p)
        session.flush()
        for j in range(3):
            m = Memory(
                type=MemoryType.PATTERN.value,
                maturity=MaturityLevel.VALIDATED.value,
                title=f"m-{i}-{j}",
                content=f"c-{i}-{j}",
                tags=["x"],
                confidence_score=0.5,
            )
            session.add(m)
            session.flush()
            session.add(ProjectMemory(project_id=p.id, memory_id=m.id))
    session.commit()

    queries = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, ctx, executemany):
        # ignore FTS/trigger noise
        if statement.strip().upper().startswith(("SELECT", "WITH")):
            queries.append(statement[:80])

    # Replicate the /projects endpoint logic
    from memee.api.routes.api_v1 import list_projects

    t0 = time.perf_counter()
    result = list_projects(session)  # type: ignore[arg-type]
    elapsed = (time.perf_counter() - t0) * 1000

    event.remove(engine, "before_cursor_execute", _count)
    session.close()

    return {
        "projects": len(result),
        "queries": len(queries),
        "elapsed_ms": round(elapsed, 2),
        "direction": "lower",
        "correct": 1.0 if len(queries) <= 3 else 0.0,
    }


# ── Scope leak: search must honor visible_memories even when caller omits scope ──
def bench_scope_default_applied():
    """Register a visible_memories hook that hides half the corpus. With a
    current_user_id registered, search_memories must apply the filter even
    when caller didn't pass scope=/user_id=."""
    from memee import plugins
    from memee.engine.search import search_memories
    from memee.storage.models import Memory

    engine, session, org = _fresh_db()
    _seed(session, org, n_patterns=20, n_anti=0)

    # Pick half the memory ids as "visible"
    all_ids = [m.id for m in session.query(Memory).all()]
    visible = set(all_ids[:10])

    original_visible = plugins.get("visible_memories")
    original_user = plugins.get("current_user_id")

    def fake_visible(sess, base_query=None):
        from memee.storage.models import Memory as M
        q = base_query if base_query is not None else sess.query(M)
        return q.filter(M.id.in_(visible))

    def fake_user(*args, **kwargs):
        return "bench-user"

    plugins.register("visible_memories", fake_visible)
    plugins.register("current_user_id", fake_user)
    try:
        # Caller omits scope/user_id — old behavior skips the filter entirely
        results = search_memories(session, "testing", limit=20, use_vectors=False)
        result_ids = {r["memory"].id for r in results}
        leaked = result_ids - visible
    finally:
        plugins.register("visible_memories", original_visible)
        plugins.register("current_user_id", original_user)
        session.close()

    return {
        "leaked_count": len(leaked),
        "total_results": len(result_ids),
        "correct": 1.0 if not leaked else 0.0,
        "direction": "higher",
    }


# ── Perf: BM25 latency over 1000 memories, vectors OFF (cold start isolation) ──
def bench_bm25_latency_1000():
    from memee.engine.search import search_memories
    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    for i in range(1000):
        session.add(
            Memory(
                type=MemoryType.PATTERN.value,
                maturity=MaturityLevel.VALIDATED.value,
                title=f"p{i} {['testing','db','perf','api','security'][i % 5]} usage {i}",
                content=f"content {i} about {['pytest','postgres','bench','rest','auth'][i % 5]}",
                tags=[["testing","db","perf","api","security"][i % 5]],
                confidence_score=0.5 + (i % 5) * 0.05,
            )
        )
        if i % 200 == 0:
            session.flush()
    session.commit()

    queries = [
        "how to write unit tests for FastAPI endpoints",
        "optimize postgres queries with index",
        "benchmark latency under load",
        "REST API auth best practice",
        "security anti-pattern in Python",
    ]
    # warmup
    search_memories(session, queries[0], limit=10, use_vectors=False)
    t0 = time.perf_counter()
    for q in queries:
        search_memories(session, q, limit=10, use_vectors=False)
    elapsed = time.perf_counter() - t0
    session.close()
    return {
        "queries": len(queries),
        "total_s": round(elapsed, 4),
        "per_query_ms": round(elapsed * 1000 / len(queries), 2),
        "direction": "lower",
    }


# ── R8: Hybrid recall — vector-only matches must be findable ──
def bench_hybrid_recall():
    """A memory that matches by vector but NOT lexically must still be found.

    Seed: 1 target with rare title, 199 distractors with common token. Query
    is the COMMON token (BM25 will rank distractors high, target last/missing
    from BM25 candidates) — but the target's embedding is set to be cosine=1
    with the query embedding. With BM25-only candidate gen the target is
    invisible; with hybrid candidate union it should be found.
    """
    from memee.engine import embeddings as emb_mod
    from memee.engine import search as search_mod
    from memee.engine.search import search_memories
    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()

    # Construct deterministic 384-dim vectors so we don't need the real model.
    target_vec = [1.0] + [0.0] * 383
    distractor_vec = [0.0, 1.0] + [0.0] * 382

    target = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.VALIDATED.value,
        title="rarewordzzz unique target",
        content="this is the right answer about timeoutzzz",
        tags=["target"],
        confidence_score=0.9,
        embedding=target_vec,
    )
    session.add(target)
    for i in range(199):
        session.add(
            Memory(
                type=MemoryType.PATTERN.value,
                maturity=MaturityLevel.VALIDATED.value,
                title=f"commontoken distractor {i}",
                content=f"commontoken commontoken {i}",
                tags=["distractor"],
                confidence_score=0.5,
                embedding=distractor_vec,
            )
        )
        if i % 50 == 0:
            session.flush()
    session.commit()
    search_mod._invalidate_embedding_cache()

    # Query has no overlap with target's title/content (BM25 = 0)
    # but its "embedding" is the same as target's (cosine = 1).
    orig_embed_text = emb_mod.embed_text
    orig_get_model = emb_mod.get_model

    def fake_embed(_text):
        return target_vec

    def fake_get_model():
        # signal "model present" so _has_embeddings short-circuit doesn't matter
        return object()

    emb_mod.embed_text = fake_embed
    emb_mod.get_model = fake_get_model
    # Bypass the sentence_transformers import probe for the test
    orig_has = search_mod._has_embeddings
    search_mod._has_embeddings = lambda: True
    try:
        results = search_memories(session, "commontoken", limit=10)
    finally:
        emb_mod.embed_text = orig_embed_text
        emb_mod.get_model = orig_get_model
        search_mod._has_embeddings = orig_has

    target_in_results = any(r["memory"].id == target.id for r in results)
    target_rank = next(
        (i for i, r in enumerate(results) if r["memory"].id == target.id),
        -1,
    )
    session.close()
    return {
        "target_found": target_in_results,
        "target_rank": target_rank,
        "n_results": len(results),
        "correct": 1.0 if target_in_results else 0.0,
        "direction": "higher",
    }


# ── R8: Fallback search must respect visibility hook ──
def bench_fallback_visibility():
    """When BM25/vector both miss, _fallback_search runs LIKE. Must respect
    visible_memories hook. Old code did a direct query → leaked hidden rows."""
    from memee import plugins
    from memee.engine.search import search_memories
    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()

    # Two memories with the same SUBSTRING in their title — one visible, one
    # hidden by the registered visibility hook. BM25 won't index a single
    # rare token across both (no real tokens shared), so search lands in the
    # LIKE fallback path.
    # Both titles contain 'notrealtoken' as a single FTS-indexed word. The
    # query 'trealt' is a SUBSTRING — FTS5 won't find it (token boundaries),
    # but the LIKE fallback will match both. That forces _fallback_search.
    visible = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.VALIDATED.value,
        title="visible row notrealtoken alpha",
        content="visible content",
        tags=["x"],
        confidence_score=0.6,
    )
    hidden = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.VALIDATED.value,
        title="hidden row notrealtoken beta",
        content="hidden content",
        tags=["x"],
        confidence_score=0.6,
    )
    session.add_all([visible, hidden])
    session.commit()

    visible_ids = {visible.id}
    orig_visible = plugins.get("visible_memories")
    orig_user = plugins.get("current_user_id")

    def fake_visible(sess, base_query=None, user_id=None):
        from memee.storage.models import Memory as M
        q = base_query if base_query is not None else sess.query(M)
        return q.filter(M.id.in_(visible_ids))

    def fake_user(*args, **kwargs):
        return "bench-user"

    plugins.register("visible_memories", fake_visible)
    plugins.register("current_user_id", fake_user)
    leaked = False
    found_count = 0
    try:
        # 'zzqq' is rare but appears as part of token "zzqq" in both titles —
        # FTS5 may or may not match depending on tokenization; rely on LIKE
        # path triggering for both rows. Using 'zzqq exact fallback' phrase
        # without sanitisation to ensure FTS sees no proper tokens.
        results = search_memories(
            session, "trealt", limit=10, use_vectors=False
        )
        # Capture ids BEFORE we tear down the session in finally; otherwise
        # accessing memory.id after close triggers a SQLAlchemy refresh and
        # raises DetachedInstanceError.
        found_ids = {r["memory"].id for r in results}
        found_count = len(found_ids)
        leaked = hidden.id in found_ids
    finally:
        plugins.register("visible_memories", orig_visible)
        plugins.register("current_user_id", orig_user)
        session.close()

    return {
        "found_count": found_count,
        "leaked_hidden": leaked,
        "correct": 0.0 if leaked else 1.0,
        "direction": "higher",
    }


# ── R8: /agents N+1 query count ──
def bench_agents_n1():
    """500 agents, current /agents endpoint runs ~1001 queries (1 + 2*N).
    Target: ≤ 3 queries with grouped aggregation."""
    from sqlalchemy import event

    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    AGENTS = [f"agent-{i}" for i in range(50)]
    for i in range(500):
        session.add(
            Memory(
                type=MemoryType.PATTERN.value,
                maturity=MaturityLevel.VALIDATED.value,
                title=f"m {i}",
                content=f"c {i}",
                tags=["x"],
                confidence_score=0.5 + (i % 5) * 0.05,
                source_agent=AGENTS[i % len(AGENTS)],
            )
        )
        if i % 100 == 0:
            session.flush()
    session.commit()

    queries: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, ctx, executemany):
        if statement.strip().upper().startswith(("SELECT", "WITH")):
            queries.append(statement[:80])

    from memee.api.routes.api_v1 import list_agents

    t0 = time.perf_counter()
    result = list_agents(session)  # type: ignore[arg-type]
    elapsed = (time.perf_counter() - t0) * 1000
    event.remove(engine, "before_cursor_execute", _count)
    session.close()

    return {
        "agents": len(result),
        "queries": len(queries),
        "elapsed_ms": round(elapsed, 2),
        "direction": "lower",
        "correct": 1.0 if len(queries) <= 3 else 0.0,
    }


# ── R8: Single-hit vector normalization ──
def bench_single_hit_norm():
    """When vector retrieves ONE candidate that matches, normalization should
    NOT collapse it to score=0. Old code: max==min → 0/range → 0.0. Fix:
    use raw cosine for n<3."""
    from memee.engine.search import _vector_rerank
    from memee.engine import embeddings as emb_mod
    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    vec = [1.0] + [0.0] * 383
    m = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.VALIDATED.value,
        title="single hit",
        content="single",
        tags=["x"],
        confidence_score=0.5,
        embedding=vec,
    )
    session.add(m)
    session.commit()

    orig_embed = emb_mod.embed_text
    emb_mod.embed_text = lambda _t: vec
    try:
        result = _vector_rerank(session, "anything", [m.id])
    finally:
        emb_mod.embed_text = orig_embed
    score = result.get(m.id, 0.0)
    session.close()
    return {
        "score": round(score, 4),
        "correct": 1.0 if score >= 0.9 else 0.0,
        "direction": "higher",
    }


# ── R8: Hook contract — base_query must compose ──
def bench_hook_compose_contract():
    """A team-style hook that ignores base_query throws the candidate filter
    away. apply_visibility should fail closed (raise) instead of leaking
    unrelated rows past the candidate filter."""
    from memee import plugins
    from memee.engine.search import search_memories
    from memee.storage.models import Memory, MemoryType, MaturityLevel

    engine, session, org = _fresh_db()
    on_topic = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.VALIDATED.value,
        title="timeout pattern target",
        content="timeout matters",
        tags=["t"],
        confidence_score=0.7,
    )
    off_topic = Memory(
        type=MemoryType.PATTERN.value,
        maturity=MaturityLevel.VALIDATED.value,
        title="payroll calculation rules",
        content="payroll computes hours worked and tax withholdings",
        tags=["payroll"],
        confidence_score=0.7,
    )
    session.add_all([on_topic, off_topic])
    session.commit()

    orig_visible = plugins.get("visible_memories")
    orig_user = plugins.get("current_user_id")

    def bad_hook(sess, base_query=None, user_id=None):
        # Simulates a hook that ignores base_query and returns a fresh global
        # query. Without the compose-contract intersect, this leaks unrelated
        # rows past the candidate filter built by search_memories.
        from memee.storage.models import Memory as M
        return sess.query(M)

    plugins.register("visible_memories", bad_hook)
    plugins.register("current_user_id", lambda *a, **kw: "bench-user")

    raised = False
    leaked = False
    try:
        results = search_memories(session, "timeout", limit=10, use_vectors=False)
        ids = {r["memory"].id for r in results}
        leaked = off_topic.id in ids
    except plugins.LicenseRequiredError:
        raised = True
    finally:
        plugins.register("visible_memories", orig_visible)
        plugins.register("current_user_id", orig_user)
        session.close()

    # Either fail-closed (raise) OR keep the candidate filter (no leak) is
    # acceptable. Both prove the contract is enforced. Old behaviour:
    # off-topic rows leaked silently.
    return {
        "raised": raised,
        "leaked": leaked,
        "correct": 1.0 if (raised or not leaked) else 0.0,
        "direction": "higher",
    }


SCENARIOS = {
    "telemetry_caller_safety": bench_telemetry_caller_safety,
    "filtered_search_type": bench_filtered_search,
    "filtered_search_maturity": bench_filtered_search_maturity,
    "router_expansion_precision": bench_router_expansion_precision,
    "review_false_positive": bench_review_false_positive,
    "projects_n1_query_count": bench_projects_n1,
    "scope_default_applied": bench_scope_default_applied,
    "bm25_latency_1000": bench_bm25_latency_1000,
    # R8
    "hybrid_recall": bench_hybrid_recall,
    "fallback_visibility": bench_fallback_visibility,
    "agents_n1_query_count": bench_agents_n1,
    "single_hit_vector_norm": bench_single_hit_norm,
    "hook_compose_contract": bench_hook_compose_contract,
}


def run(label: str):
    results = {}
    for name, fn in SCENARIOS.items():
        print(f"[{label}] {name} ...", flush=True)
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = {"error": str(e), "correct": 0.0, "direction": "higher"}
        print(f"  → {results[name]}", flush=True)
    out = OUT_DIR / f"{label}.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nSaved: {out}")


def compare():
    before = json.loads((OUT_DIR / "before.json").read_text())
    after = json.loads((OUT_DIR / "after.json").read_text())
    print("\n" + "=" * 72)
    print(f"{'scenario':<34} {'before':>15} {'after':>15} {'Δ':>8}")
    print("=" * 72)
    for name in SCENARIOS:
        b = before.get(name, {})
        a = after.get(name, {})

        def _primary(d):
            # prefer "correct" if present, else the main numeric value
            if "correct" in d:
                return ("correct", d["correct"])
            for k in ("per_query_ms", "queries", "fp_rate", "elapsed_ms", "total_s"):
                if k in d:
                    return (k, d[k])
            return ("?", None)

        kb, vb = _primary(b)
        ka, va = _primary(a)
        key = kb if kb == ka else f"{kb}/{ka}"
        delta = ""
        if isinstance(vb, (int, float)) and isinstance(va, (int, float)):
            if vb != 0:
                delta = f"{(va - vb) / vb * 100:+.1f}%"
            else:
                delta = f"+{va:.2f}"
        print(f"{name:<34} {str(vb):>15} {str(va):>15} {delta:>8}")
    print("=" * 72)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: bench_autoresearch.py before|after|compare")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "compare":
        compare()
    else:
        run(cmd)
