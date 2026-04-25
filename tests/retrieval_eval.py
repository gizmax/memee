"""Mini BEIR-style retrieval eval for Memee.

Why this file exists
--------------------
The bench harness in ``bench_autoresearch.py`` proves correctness — did the
fix close the regression? — but it doesn't tell us whether the *ranker* got
better or worse on real queries. nDCG / MRR / Recall@k are the standard IR
metrics for that. Memee's queries are agent tasks ("optimize N+1 in Django
ORM"), and the relevance label is "this memory unblocked this task," so we
roll our own seed set rather than borrow BEIR's news/QA corpora.

Corpus / queries
----------------
~255 memories spanning 10 domains (testing, db, api, security, perf,
frontend, ops, ml, decisions, lessons) plus the R12 expansion (rare
identifiers, multi-language, lexical-gap intent). 200+ labelled queries
with graded relevance ∈ {1, 2, 3} where 3 = perfect match (the one canon
answer), 2 = strong supporter, 1 = topical neighbour.

R12 expansion: every query carries a ``cluster`` tag. Seven clusters
stratify difficulty so future ranker work can detect 1-2 nDCG point
swings instead of being averaged out at the macro level:

    * ``code_specific``       — rare identifiers (BM25 should dominate)
    * ``paraphrastic``        — same intent, no shared identifier
    * ``anti_pattern_intent`` — fix/avoid/never verbs → AP gold
    * ``onboarding_to_stack`` — "I'm new to {X}" → multiple canon hits
    * ``diff_review``         — pasted diff hunk → matching AP
    * ``multilingual_lite``   — EN + CS/DE same-intent pairs
    * ``lexical_gap_hard``    — adversarial: zero token overlap with gold

Run:
    .venv/bin/python -m tests.retrieval_eval                        # print JSON
    .venv/bin/python -m tests.retrieval_eval --save bm25_only       # write .bench/eval_bm25_only.json
    .venv/bin/python -m tests.retrieval_eval \\
        --compare-with .bench/eval_bm25_only.json                   # diff + per-cluster permutation_test
    .venv/bin/python -m tests.retrieval_eval --cluster paraphrastic # restrict to one cluster
    .venv/bin/python -m tests.retrieval_eval --verbose              # per-query breakdown

Output is a single JSON line per metric (suitable for diffing across
branches). ``--save LABEL`` writes ``.bench/eval_<LABEL>.json``;
``--compare-with PATH`` prints a Δ table against an earlier run.
"""

from __future__ import annotations

import json
import math
import os
import random
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("MEMEE_TELEMETRY", "0")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


# ── Maturity multiplier mirror (kept in sync with engine/search.py) ─────────
# Used by ``maturity_bias@5`` so we can inspect whether the ranker drifted
# into hypothesis-tier results without re-importing the engine module just
# for the constant.
MATURITY_MULTIPLIER = {
    "canon": 1.0,
    "validated": 0.85,
    "tested": 0.65,
    "hypothesis": 0.4,
    "deprecated": 0.05,
}


# ── Seed corpus: ~150 memories spanning 10 domains ──────────────────────────
# Each entry has a stable short id (domain prefix + number) so that query
# labels stay valid as the corpus grows. Type distribution targets:
#   pattern ~70 % | anti_pattern ~15 % | lesson ~8 % | decision ~5 % | obs ~2 %
# Maturity distribution targets:
#   canon 30 % | validated 50 % | tested 15 % | hypothesis 5 %


CORPUS: list[dict] = [
    # ── Testing (14) ────────────────────────────────────────────────────────
    {"id": "test01", "type": "pattern", "maturity": "canon",
     "title": "Always use pytest fixtures over global state",
     "content": "Fixtures are scoped, composable, and explicit. Global mocks become invisible coupling that breaks when test order changes; fixtures isolate setup per test.",
     "tags": ["testing", "pytest", "python"]},
    {"id": "test02", "type": "anti_pattern", "maturity": "canon",
     "title": "Never mock the database in integration tests",
     "content": "Mock/prod divergence masks broken migrations and constraint violations. Use a real ephemeral SQLite (or testcontainers Postgres) so the integration test exercises the real driver.",
     "tags": ["testing", "database", "integration"]},
    {"id": "test03", "type": "pattern", "maturity": "validated",
     "title": "Snapshot-based assertions for complex outputs",
     "content": "When an output has 50 fields, snapshot diffs are easier to maintain than 50 asserts. Update snapshot on intentional changes; review the diff in PR.",
     "tags": ["testing", "snapshot"]},
    {"id": "test04", "type": "lesson", "maturity": "validated",
     "title": "Property-based tests catch edge cases unit tests miss",
     "content": "Hypothesis found a unicode normalization bug we'd had in production for a year. Use property-based tests for parsers, serializers, and any pure function with a wide input domain.",
     "tags": ["testing", "hypothesis", "property"]},
    {"id": "test05", "type": "pattern", "maturity": "validated",
     "title": "Use freezegun for time-sensitive tests",
     "content": "datetime.now() makes tests flaky across midnight, DST, and slow CI. Freeze time at a known instant so assertions are deterministic.",
     "tags": ["testing", "time", "flaky"]},
    {"id": "test06", "type": "anti_pattern", "maturity": "canon",
     "title": "Sleeping in tests to wait for async work",
     "content": "time.sleep(1) is the #1 source of flake. Poll with a deadline, use an event/queue, or expose a hook. Sleeps make CI 10x slower and still flake under load.",
     "tags": ["testing", "flaky", "async"]},
    {"id": "test07", "type": "pattern", "maturity": "validated",
     "title": "Factory fixtures over hard-coded test data",
     "content": "factory_boy / Pydantic factories produce realistic objects with overridable fields. Hard-coded literals drift from the schema and break on every migration.",
     "tags": ["testing", "fixtures", "factory"]},
    {"id": "test08", "type": "pattern", "maturity": "tested",
     "title": "Mock at the boundary, not inside business logic",
     "content": "Mock HTTP/DB at the edge (requests adapter, sqlalchemy engine). Mocking domain functions hides real coupling and tests the mock, not the code.",
     "tags": ["testing", "mocks", "boundaries"]},
    {"id": "test09", "type": "pattern", "maturity": "validated",
     "title": "Branch coverage over line coverage",
     "content": "Line coverage hides untested branches in if/else and exception paths. Use --cov-branch to surface missed conditions; aim for 85%+ branch coverage.",
     "tags": ["testing", "coverage", "ci"]},
    {"id": "test10", "type": "pattern", "maturity": "canon",
     "title": "Run pytest with -x in dev, full suite in CI",
     "content": "Fail-fast locally surfaces the first regression in seconds; CI runs the full suite to catch shadowed failures and report on flaky tests.",
     "tags": ["testing", "pytest", "ci"]},
    {"id": "test11", "type": "lesson", "maturity": "validated",
     "title": "End-to-end tests should number in dozens, not thousands",
     "content": "E2E tests are slow and flaky. Pyramid: lots of unit tests, fewer integration, dozens of E2E for critical user journeys only.",
     "tags": ["testing", "e2e", "pyramid"]},
    {"id": "test12", "type": "pattern", "maturity": "tested",
     "title": "Use pytest-benchmark for regression-sensitive code",
     "content": "Latency-sensitive code paths get a pytest-benchmark assertion with a tolerance band. Regressions show up as a CI failure, not a vague slowness ticket.",
     "tags": ["testing", "benchmark", "perf"]},
    {"id": "test13", "type": "anti_pattern", "maturity": "validated",
     "title": "Tests that depend on test ordering",
     "content": "Hidden state (class attrs, module globals, DB rows) makes tests pass alone, fail in suite. Use pytest --randomize and isolated fixtures.",
     "tags": ["testing", "isolation", "flaky"]},
    {"id": "test14", "type": "pattern", "maturity": "validated",
     "title": "Tag slow tests and exclude from default run",
     "content": "@pytest.mark.slow on >1s tests; default `pytest -m 'not slow'`. Devs run slow suite before PR; CI runs everything. Keeps the inner loop fast.",
     "tags": ["testing", "pytest", "ci"]},

    # ── Database (17) ───────────────────────────────────────────────────────
    {"id": "db01", "type": "anti_pattern", "maturity": "canon",
     "title": "N+1 queries in ORM loops",
     "content": "for x in q.all(): x.related triggers 1+N queries. Use joinedload (single query w/ JOIN) or selectinload (one extra query) for the relation.",
     "tags": ["database", "orm", "perf"]},
    {"id": "db02", "type": "pattern", "maturity": "canon",
     "title": "WAL mode for SQLite under concurrent writers",
     "content": "PRAGMA journal_mode=WAL allows readers and one writer concurrently. Default rollback journal blocks readers during a write. Required for any web service.",
     "tags": ["database", "sqlite", "wal", "concurrency"]},
    {"id": "db03", "type": "pattern", "maturity": "validated",
     "title": "Composite index on (org_id, created_at) for time-bounded scans",
     "content": "Single-column indexes can't satisfy ORDER BY + WHERE in one pass; the planner falls back to a sort. Composite index removes the sort and cuts query time 10-100x.",
     "tags": ["database", "index", "postgres"]},
    {"id": "db04", "type": "decision", "maturity": "validated",
     "title": "Use SQLite over Postgres for single-tenant tools",
     "content": "Operational simplicity wins until you outgrow it. WAL gives you concurrency, FTS5 gives you search, and migration to Postgres is straightforward via dump/load.",
     "tags": ["database", "sqlite", "decision"]},
    {"id": "db05", "type": "pattern", "maturity": "validated",
     "title": "Use selectinload for one-to-many relationships",
     "content": "selectinload issues one extra IN query per relation regardless of result count. joinedload duplicates parent rows; selectinload avoids the cartesian blowup for collections.",
     "tags": ["database", "orm", "sqlalchemy"]},
    {"id": "db06", "type": "anti_pattern", "maturity": "canon",
     "title": "SELECT * in production queries",
     "content": "Pulls every column over the wire even when you need three. Breaks when a column is added, slows large rows, defeats covering indexes. Always project explicit columns.",
     "tags": ["database", "sql", "perf"]},
    {"id": "db07", "type": "pattern", "maturity": "canon",
     "title": "Always wrap multi-statement work in a transaction",
     "content": "Without BEGIN/COMMIT each statement is its own transaction. A crash mid-sequence leaves partial state. Even read-only sequences benefit from a snapshot.",
     "tags": ["database", "transactions", "consistency"]},
    {"id": "db08", "type": "lesson", "maturity": "validated",
     "title": "Run EXPLAIN before adding an index",
     "content": "Indexes cost write throughput and disk. EXPLAIN ANALYZE shows whether the planner is actually scanning vs seeking. Add only when the query plan confirms the need.",
     "tags": ["database", "index", "explain"]},
    {"id": "db09", "type": "pattern", "maturity": "validated",
     "title": "Migrations are forward-only; never edit a shipped migration",
     "content": "Editing a migration that prod already ran creates drift between dev and prod schemas. Always add a new migration that fixes the prior one.",
     "tags": ["database", "migrations", "alembic"]},
    {"id": "db10", "type": "pattern", "maturity": "validated",
     "title": "Connection pool sizing: 2*workers + headroom",
     "content": "Default pool of 5 starves under load. Tune to 2*N_workers + 4 to handle bursts and slow queries without queueing. Watch for pool checkout latency.",
     "tags": ["database", "pool", "perf"]},
    {"id": "db11", "type": "anti_pattern", "maturity": "validated",
     "title": "Long-running transactions in web requests",
     "content": "A 30s transaction holds locks, bloats MVCC, and blocks vacuum. Keep web request transactions <1s; for batch work use chunked, committed batches.",
     "tags": ["database", "transactions", "locks"]},
    {"id": "db12", "type": "pattern", "maturity": "tested",
     "title": "Use pgvector or sqlite-vec for embedding search",
     "content": "Native vector indexes beat brute-force cosine at >10K embeddings. pgvector ivfflat/hnsw, sqlite-vec for embedded use; both integrate with SQL filters.",
     "tags": ["database", "vector", "embeddings"]},
    {"id": "db13", "type": "pattern", "maturity": "validated",
     "title": "Idempotent upserts via ON CONFLICT",
     "content": "INSERT ... ON CONFLICT (key) DO UPDATE SET ... is atomic. Beats SELECT-then-INSERT/UPDATE which races under concurrency.",
     "tags": ["database", "postgres", "idempotent"]},
    {"id": "db14", "type": "lesson", "maturity": "validated",
     "title": "FTS5 contentless tables save 50% disk for search",
     "content": "Contentless FTS5 stores only the index, not the indexed text; pair with a base table the trigger reads from. Cuts a 4 GB DB to ~2 GB on text-heavy data.",
     "tags": ["database", "sqlite", "fts5"]},
    {"id": "db15", "type": "pattern", "maturity": "tested",
     "title": "Read replicas for analytical queries",
     "content": "Route long aggregates to a read replica with a sane lag tolerance. Keeps OLTP latency tight; analytics no longer hold MVCC bloat on the primary.",
     "tags": ["database", "replication", "scale"]},
    {"id": "db16", "type": "anti_pattern", "maturity": "validated",
     "title": "Sharding before vertical scaling",
     "content": "Sharding adds operational complexity (cross-shard joins, rebalancing). Most teams hit the wall at 10 TB+; below that, bigger boxes and read replicas win.",
     "tags": ["database", "sharding", "scale"]},
    {"id": "db17", "type": "decision", "maturity": "canon",
     "title": "Standardize on Postgres for primary store",
     "content": "Reliability + extensions (pgvector, pg_partman, postgis) + operational maturity beat the ergonomic wins of newer DBs. Defer specialty stores until the workload demands them.",
     "tags": ["database", "postgres", "decision"]},

    # ── API (17) ────────────────────────────────────────────────────────────
    {"id": "api01", "type": "pattern", "maturity": "canon",
     "title": "Always set HTTP timeout on outbound requests",
     "content": "requests.get(url, timeout=10). Without it, an upstream hang blocks the worker forever and exhausts the pool. Default to a tight timeout and bump per-call when needed.",
     "tags": ["http", "timeout", "reliability"]},
    {"id": "api02", "type": "anti_pattern", "maturity": "canon",
     "title": "Catching bare except in API handlers",
     "content": "Eats KeyboardInterrupt, SystemExit, and the actual bug. Catch the specific exception class; let unexpected errors bubble to the framework's error handler.",
     "tags": ["api", "exceptions", "python"]},
    {"id": "api03", "type": "pattern", "maturity": "canon",
     "title": "Pydantic v2 for FastAPI request validation",
     "content": "Auto-generates OpenAPI docs, catches type errors at the boundary, integrates with FastAPI dependency injection. v2 is 5-50x faster than v1 (Rust core).",
     "tags": ["api", "pydantic", "fastapi", "validation"]},
    {"id": "api04", "type": "lesson", "maturity": "canon",
     "title": "Idempotency keys prevent duplicate writes from retries",
     "content": "Client retries are inevitable; require an Idempotency-Key header on POST. Store the key + response for 24h; replay returns the same body, not a new write.",
     "tags": ["api", "idempotency", "retries"]},
    {"id": "api05", "type": "pattern", "maturity": "validated",
     "title": "Version your API in the URL or Accept header",
     "content": "/v1/, /v2/ in the path is the simplest. Accept: application/vnd.api+json;v=2 is cleaner but harder to test. Pick one and document the deprecation policy.",
     "tags": ["api", "versioning", "rest"]},
    {"id": "api06", "type": "pattern", "maturity": "canon",
     "title": "Rate limit by token-bucket per principal",
     "content": "Per-IP limits punish corporate NATs; per-user/per-API-key isolates abusive callers. 429 + Retry-After tells well-behaved clients when to come back.",
     "tags": ["api", "rate-limit", "security"]},
    {"id": "api07", "type": "anti_pattern", "maturity": "validated",
     "title": "Returning HTTP 200 with an error JSON body",
     "content": "Breaks every HTTP-aware client and load balancer health check. Use 4xx for client errors, 5xx for server errors; put detail in the body, not the status.",
     "tags": ["api", "http", "rest"]},
    {"id": "api08", "type": "pattern", "maturity": "validated",
     "title": "GraphQL DataLoader for N+1 in resolvers",
     "content": "Each resolver firing its own DB query produces N+1 across nested fields. DataLoader batches loads per request and dedupes by key.",
     "tags": ["api", "graphql", "perf"]},
    {"id": "api09", "type": "pattern", "maturity": "tested",
     "title": "gRPC with reflection enabled in non-prod",
     "content": "grpcurl + reflection lets devs call services without distributing .proto files. Disable in prod for surface-area reduction.",
     "tags": ["api", "grpc", "developer-experience"]},
    {"id": "api10", "type": "pattern", "maturity": "canon",
     "title": "OpenAPI spec is the contract, not docs",
     "content": "Generate the spec from code (FastAPI, drf-spectacular). Spec drives client SDKs, contract tests, and the API explorer. Hand-written docs always drift.",
     "tags": ["api", "openapi", "rest"]},
    {"id": "api11", "type": "anti_pattern", "maturity": "validated",
     "title": "Returning unbounded list endpoints",
     "content": "GET /users returning all users tanks once you cross 10K. Always paginate (cursor or limit/offset) and document the page size cap.",
     "tags": ["api", "pagination", "perf"]},
    {"id": "api12", "type": "pattern", "maturity": "validated",
     "title": "Cursor pagination over offset for stable lists",
     "content": "Offset pagination skips/duplicates rows when the list mutates between pages. Cursor (id+timestamp) is stable and faster on large tables.",
     "tags": ["api", "pagination", "stability"]},
    {"id": "api13", "type": "pattern", "maturity": "validated",
     "title": "Use OAuth2 client credentials for service-to-service",
     "content": "Long-lived API keys leak via logs and screenshots. OAuth2 client credentials gets you short-lived tokens, scoped audiences, and rotation.",
     "tags": ["api", "auth", "oauth"]},
    {"id": "api14", "type": "pattern", "maturity": "validated",
     "title": "Surface request id in every response and log",
     "content": "X-Request-Id header echoed back; every log line in the request includes it. Customer reports an issue: grep one id, see the whole timeline.",
     "tags": ["api", "tracing", "ops"]},
    {"id": "api15", "type": "decision", "maturity": "canon",
     "title": "Adopt FastAPI over Flask for new async API services",
     "content": "Native async, auto OpenAPI, Pydantic validation. Migration cost paid back in one quarter on a 20-endpoint service; perf 3x for IO-bound workloads.",
     "tags": ["api", "fastapi", "decision"]},
    {"id": "api16", "type": "pattern", "maturity": "tested",
     "title": "Health endpoint returns version + git sha",
     "content": "/healthz returns 200 + {version, sha, started_at}. Operators see at a glance which build is live; CDNs and LBs use it for liveness.",
     "tags": ["api", "ops", "health"]},
    {"id": "api17", "type": "anti_pattern", "maturity": "validated",
     "title": "Putting auth tokens in query strings",
     "content": "Query strings land in access logs, browser history, and Referer headers. Use Authorization header; if you must use query for downloads, sign and short-expire.",
     "tags": ["api", "security", "auth"]},

    # ── Security (16) ───────────────────────────────────────────────────────
    {"id": "sec01", "type": "anti_pattern", "maturity": "canon",
     "title": "eval() on user input is RCE",
     "content": "Even sandboxed eval is bypassable. For literal parsing use ast.literal_eval; for expressions use a real parser (lark, simpleeval) with a whitelist.",
     "tags": ["security", "eval", "rce"]},
    {"id": "sec02", "type": "pattern", "maturity": "canon",
     "title": "Hash passwords with argon2id",
     "content": "argon2id parameters: t=2, m=65536 KiB, p=1 in 2024. Bcrypt remains acceptable for legacy at cost factor 12. Never SHA-256 a password directly.",
     "tags": ["security", "auth", "password"]},
    {"id": "sec03", "type": "anti_pattern", "maturity": "canon",
     "title": "Storing secrets in environment variables in containerized envs",
     "content": "ENV is readable by anything in the container (including third-party libs that crash-dump env). Use a secret manager (Vault, AWS SSM) or a tmpfs mount.",
     "tags": ["security", "secrets", "docker"]},
    {"id": "sec04", "type": "lesson", "maturity": "validated",
     "title": "CSP nonces beat allowlists for inline scripts",
     "content": "Nonces work even when the inline script is dynamic; allowlists fight every new vendor. Generate per-request, embed in <script nonce=...> and CSP header.",
     "tags": ["security", "csp", "frontend"]},
    {"id": "sec05", "type": "pattern", "maturity": "canon",
     "title": "Validate JWT signature AND audience AND expiry",
     "content": "Many JWT libs default to no audience check. A token from another service in the same auth realm passes. Always assert iss/aud/exp explicitly.",
     "tags": ["security", "jwt", "auth"]},
    {"id": "sec06", "type": "anti_pattern", "maturity": "canon",
     "title": "Using JWT alg=none or HS256 with public secrets",
     "content": "alg=none is the original JWT bug; HS256 with a known secret (or one in env-leaked logs) is forgeable. Use RS256/EdDSA for inter-service.",
     "tags": ["security", "jwt", "crypto"]},
    {"id": "sec07", "type": "pattern", "maturity": "validated",
     "title": "Rotate API keys on a schedule, not just on incident",
     "content": "Quarterly rotation surfaces broken automation that hard-codes keys. Pair with overlapping validity windows so rotation isn't a flag day.",
     "tags": ["security", "auth", "rotation"]},
    {"id": "sec08", "type": "pattern", "maturity": "validated",
     "title": "Audit log every privileged action with actor + diff",
     "content": "Who, what, when, what changed (before/after). Append-only store. Required for SOC 2 / ISO 27001; invaluable for incident forensics.",
     "tags": ["security", "audit", "compliance"]},
    {"id": "sec09", "type": "pattern", "maturity": "canon",
     "title": "Parameterize SQL — never f-string user input",
     "content": "f\"SELECT * FROM x WHERE id={uid}\" is SQL injection. ORM ? placeholders, psycopg %s, or SQLAlchemy text(:id) bind params. No exceptions.",
     "tags": ["security", "sql", "injection"]},
    {"id": "sec10", "type": "anti_pattern", "maturity": "canon",
     "title": "Disabling TLS verification to make tests pass",
     "content": "verify=False or InsecureSkipVerify in test config leaks into prod via copy-paste. Use a self-signed CA your client trusts in test, real cert in prod.",
     "tags": ["security", "tls", "testing"]},
    {"id": "sec11", "type": "pattern", "maturity": "validated",
     "title": "Right-to-deletion: cascade plus audit row",
     "content": "GDPR Article 17. CASCADE the user's PII; replace with a tombstone row that records when and why. Don't break referential integrity in finance/audit tables.",
     "tags": ["security", "gdpr", "privacy"]},
    {"id": "sec12", "type": "pattern", "maturity": "validated",
     "title": "OWASP top-10 review on every PR that touches auth or input",
     "content": "Ten line checklist on the PR template: A01 access control, A03 injection, A07 auth/session. Cheap; catches most regressions.",
     "tags": ["security", "owasp", "review"]},
    {"id": "sec13", "type": "pattern", "maturity": "validated",
     "title": "Rate limit auth endpoints aggressively",
     "content": "Login/signup/forgot-password are credential-stuffing targets. 5/min/IP + per-account exponential backoff. Pair with breached-password check.",
     "tags": ["security", "auth", "rate-limit"]},
    {"id": "sec14", "type": "lesson", "maturity": "validated",
     "title": "Threat-model new features before they ship",
     "content": "30-min STRIDE review on any feature that handles auth, money, or PII. Found a CSRF gap on payment confirm we'd otherwise have shipped.",
     "tags": ["security", "threat-model", "process"]},
    {"id": "sec15", "type": "anti_pattern", "maturity": "validated",
     "title": "Logging full request bodies including auth headers",
     "content": "Access logs become a credential dump if the WAF compromises. Strip Authorization, Cookie, and known PII fields before structured logging.",
     "tags": ["security", "logging", "privacy"]},
    {"id": "sec16", "type": "pattern", "maturity": "tested",
     "title": "Use OAuth2 PKCE for public clients",
     "content": "Mobile/SPA clients can't keep a secret. PKCE (code_challenge + code_verifier) prevents auth-code interception; supported by every major IdP.",
     "tags": ["security", "oauth", "mobile"]},

    # ── Performance (13) ────────────────────────────────────────────────────
    {"id": "perf01", "type": "pattern", "maturity": "canon",
     "title": "Profile before optimizing",
     "content": "cProfile / py-spy on the actual workload usually points to a single hot path. Optimize that first; everything else is noise. Premature optimization is bug-rich.",
     "tags": ["perf", "profiling"]},
    {"id": "perf02", "type": "lesson", "maturity": "canon",
     "title": "Connection pool exhaustion under load",
     "content": "Default pool of 5 starves a FastAPI worker behind a reverse proxy. Tune to 2*workers + 4. Watch checkout latency to know when to bump.",
     "tags": ["perf", "pool", "fastapi"]},
    {"id": "perf03", "type": "pattern", "maturity": "validated",
     "title": "Cache invalidation by event, not TTL, when freshness matters",
     "content": "TTL is fine for slow-changing data. For trade prices or feature flags use pub/sub invalidation; readers see fresh data within ms of a write.",
     "tags": ["perf", "cache", "invalidation"]},
    {"id": "perf04", "type": "pattern", "maturity": "validated",
     "title": "Async IO for fan-out network calls",
     "content": "10 sequential 100ms calls = 1s wall time. asyncio.gather them = 100ms. Only meaningful for IO-bound; CPU-bound work needs threads/processes.",
     "tags": ["perf", "async", "python"]},
    {"id": "perf05", "type": "pattern", "maturity": "validated",
     "title": "Lazy-import heavy dependencies",
     "content": "Module-level `import torch` adds 2-4s to every CLI invocation. Move into the function that uses it; CLI startup drops to sub-second.",
     "tags": ["perf", "startup", "python"]},
    {"id": "perf06", "type": "anti_pattern", "maturity": "validated",
     "title": "Unbounded in-memory cache",
     "content": "dict-as-cache without an eviction policy grows until OOM. Use functools.lru_cache(maxsize=N) or a real LRU; monitor cache size in metrics.",
     "tags": ["perf", "cache", "memory"]},
    {"id": "perf07", "type": "pattern", "maturity": "validated",
     "title": "Stream large responses; don't buffer in memory",
     "content": "Reading a 1 GB CSV into memory before returning OOMs the worker. StreamingResponse / generator yields rows as they're produced.",
     "tags": ["perf", "memory", "streaming"]},
    {"id": "perf08", "type": "pattern", "maturity": "tested",
     "title": "Pre-warm process state on boot",
     "content": "First request pays for ORM metadata, embedding model load, JIT compile. Hit a /warmup endpoint at deploy before flipping the LB.",
     "tags": ["perf", "startup", "deploy"]},
    {"id": "perf09", "type": "lesson", "maturity": "validated",
     "title": "Measure p99 not just average latency",
     "content": "Average latency hides the long tail. p99 is what your users feel during a GC pause or pool stall. Alert on p99 budgets.",
     "tags": ["perf", "latency", "metrics"]},
    {"id": "perf10", "type": "pattern", "maturity": "tested",
     "title": "uvloop drop-in replacement for asyncio loop",
     "content": "uvloop is 2-4x faster than the stdlib asyncio loop on most workloads. asyncio.set_event_loop_policy(uvloop.EventLoopPolicy()) at startup.",
     "tags": ["perf", "async", "python"]},
    {"id": "perf11", "type": "pattern", "maturity": "validated",
     "title": "Batch external API calls to amortize fixed cost",
     "content": "100 single-item calls at 50ms each = 5s; one 100-item batch call = 200ms. If the API supports batch endpoints, use them; otherwise queue + flush.",
     "tags": ["perf", "api", "batching"]},
    {"id": "perf12", "type": "anti_pattern", "maturity": "validated",
     "title": "JSON-encoding a huge dict in the request handler",
     "content": "json.dumps on a multi-MB dict blocks the event loop. orjson is 5x faster; for >10MB stream chunked NDJSON instead of one big blob.",
     "tags": ["perf", "json", "latency"]},
    {"id": "perf13", "type": "pattern", "maturity": "validated",
     "title": "Use ETags on cacheable GETs",
     "content": "Compute a stable hash of the response body; honour If-None-Match → 304 with empty body. Cuts bandwidth and origin load for repeat polling.",
     "tags": ["perf", "http", "cache"]},

    # ── Frontend (13) ───────────────────────────────────────────────────────
    {"id": "fe01", "type": "anti_pattern", "maturity": "canon",
     "title": "useEffect without dependency array re-runs every render",
     "content": "Either omit it (mount-only) or include every reactive dep. ESLint react-hooks/exhaustive-deps catches this; treat the warning as an error.",
     "tags": ["react", "hooks", "frontend"]},
    {"id": "fe02", "type": "pattern", "maturity": "canon",
     "title": "Tailwind utility classes over CSS-in-JS",
     "content": "No runtime cost, smaller bundle, consistent design tokens, JIT purge means you only ship classes you use. CSS-in-JS adds 10-30 KB and slows hydration.",
     "tags": ["frontend", "tailwind", "css"]},
    {"id": "fe03", "type": "pattern", "maturity": "validated",
     "title": "Server components for data-heavy reads in Next.js",
     "content": "RSC fetches at the edge; the client only ships interactive islands. Bundle drops 30-60% on dashboard-style pages.",
     "tags": ["frontend", "react", "ssr"]},
    {"id": "fe04", "type": "anti_pattern", "maturity": "validated",
     "title": "Storing server state in Redux",
     "content": "Server state has caching, revalidation, and stale-while-refetch needs that Redux doesn't model. Use TanStack Query / SWR for server state, Redux/Zustand only for client UI state.",
     "tags": ["frontend", "react", "state"]},
    {"id": "fe05", "type": "pattern", "maturity": "validated",
     "title": "Lazy-load route bundles with React.lazy + Suspense",
     "content": "Top-level lazy() imports split per-route bundles. First-paint pulls only the chunks the user needs; subsequent navigations stream on demand.",
     "tags": ["frontend", "react", "bundle"]},
    {"id": "fe06", "type": "pattern", "maturity": "validated",
     "title": "Prefer CSS Grid over flex for 2D layouts",
     "content": "Grid expresses rows + columns natively; flex is 1D and forces wrapper divs for 2D. Less DOM, easier responsive rules.",
     "tags": ["frontend", "css", "layout"]},
    {"id": "fe07", "type": "anti_pattern", "maturity": "validated",
     "title": "Mutating state directly in setState callback",
     "content": "setState(prev => { prev.x = 1; return prev }) skips re-render because reference is equal. Always return a new object.",
     "tags": ["frontend", "react", "state"]},
    {"id": "fe08", "type": "pattern", "maturity": "tested",
     "title": "Use signals/atoms over useState for cross-component state",
     "content": "Jotai/Zustand atoms avoid prop drilling and unnecessary re-renders that Context triggers. Keep useState for component-local UI state.",
     "tags": ["frontend", "react", "state"]},
    {"id": "fe09", "type": "pattern", "maturity": "validated",
     "title": "Image responsive sizing with next/image (or equivalent)",
     "content": "Serve AVIF/WebP, lazy-load below-fold, srcset for DPR. Cuts LCP by 30-60% on image-heavy pages.",
     "tags": ["frontend", "perf", "images"]},
    {"id": "fe10", "type": "pattern", "maturity": "validated",
     "title": "Accessibility: every interactive element keyboard-reachable",
     "content": "Tab order, focus rings, aria-label on icon buttons, aria-live on toasts. Run axe-core in CI; fail the build on serious violations.",
     "tags": ["frontend", "a11y"]},
    {"id": "fe11", "type": "anti_pattern", "maturity": "validated",
     "title": "Hydration mismatch from non-deterministic SSR",
     "content": "Date.now() / Math.random() in the render body produces different output server vs client; React tears down the tree. Stable values only.",
     "tags": ["frontend", "react", "ssr"]},
    {"id": "fe12", "type": "pattern", "maturity": "tested",
     "title": "Bundle analyser in CI to catch size regressions",
     "content": "Webpack/Rspack analyser report on every PR; fail the build if main bundle grows >5%. Surfaces accidental imports of moment, lodash, etc.",
     "tags": ["frontend", "bundle", "ci"]},
    {"id": "fe13", "type": "lesson", "maturity": "validated",
     "title": "useMemo / useCallback are not free",
     "content": "Memoization adds dependency tracking overhead. Profile first; only memoize expensive renders or referentially-stable callbacks passed to memoized children.",
     "tags": ["frontend", "react", "perf"]},

    # ── Ops (13) ────────────────────────────────────────────────────────────
    {"id": "ops01", "type": "lesson", "maturity": "canon",
     "title": "Health checks that hit the DB catch dead pools",
     "content": "A liveness probe that only returns 200 misses pool exhaustion and DNS failure. /readyz hits the DB + a downstream; /healthz stays cheap.",
     "tags": ["ops", "health", "kubernetes"]},
    {"id": "ops02", "type": "pattern", "maturity": "canon",
     "title": "Structured logging with request id propagation",
     "content": "Every log line in a request shares an id; debugging gets 10x easier. JSON logs with consistent keys (level, ts, request_id, msg).",
     "tags": ["ops", "logging", "tracing"]},
    {"id": "ops03", "type": "anti_pattern", "maturity": "canon",
     "title": "Skipping pre-commit hooks with --no-verify",
     "content": "If hooks fail, fix the underlying issue. Skipping is technical debt that compounds — the next dev hits a bigger fail-cluster.",
     "tags": ["ops", "ci", "git"]},
    {"id": "ops04", "type": "pattern", "maturity": "validated",
     "title": "Blue/green or canary for risky deploys",
     "content": "5% canary catches regressions before 100% sees them. Roll back by flipping the LB, not by reverting + rebuilding.",
     "tags": ["ops", "deploy", "release"]},
    {"id": "ops05", "type": "pattern", "maturity": "validated",
     "title": "SLOs over uptime percentages",
     "content": "99.9% uptime says nothing about user experience. Define SLO per critical journey (latency, success rate); error budget drives release velocity.",
     "tags": ["ops", "slo", "reliability"]},
    {"id": "ops06", "type": "pattern", "maturity": "validated",
     "title": "Dockerfile multi-stage build for smaller images",
     "content": "Build stage with toolchain, runtime stage with only the binary + libs. Cuts a 1.2 GB image to 80 MB; surface area shrinks proportionally.",
     "tags": ["ops", "docker", "image"]},
    {"id": "ops07", "type": "anti_pattern", "maturity": "validated",
     "title": "Running containers as root",
     "content": "A container escape lands as root on the host. Always USER nonroot in the Dockerfile; readonly rootfs where the app permits.",
     "tags": ["ops", "docker", "security"]},
    {"id": "ops08", "type": "pattern", "maturity": "validated",
     "title": "Resource requests + limits on every k8s pod",
     "content": "Without requests the scheduler over-packs nodes; without limits a leak takes the node. Requests = expected, limits = panic threshold.",
     "tags": ["ops", "kubernetes", "resources"]},
    {"id": "ops09", "type": "pattern", "maturity": "validated",
     "title": "Trace IDs propagated via W3C traceparent header",
     "content": "OpenTelemetry standard, supported by every major APM. Pass via incoming HTTP and outbound calls so distributed traces stitch end-to-end.",
     "tags": ["ops", "tracing", "otel"]},
    {"id": "ops10", "type": "pattern", "maturity": "tested",
     "title": "GitOps for config drift detection",
     "content": "ArgoCD/Flux reconciles cluster against git; manual kubectl edits get reverted. Drift is impossible because the repo is the source of truth.",
     "tags": ["ops", "gitops", "kubernetes"]},
    {"id": "ops11", "type": "lesson", "maturity": "validated",
     "title": "Postmortems blameless and within 5 days",
     "content": "Blame chills future incident reports; speed keeps memory fresh. Five whys, action items with owners, public to the company.",
     "tags": ["ops", "incident", "process"]},
    {"id": "ops12", "type": "pattern", "maturity": "validated",
     "title": "Backups + tested restore drill quarterly",
     "content": "Untested backups are a gambling habit. Restore to a scratch env quarterly; time it; document the runbook.",
     "tags": ["ops", "backup", "drill"]},
    {"id": "ops13", "type": "anti_pattern", "maturity": "validated",
     "title": "Logging at INFO inside a tight loop",
     "content": "Floods log storage, hides real signals, and adds 10-20μs per iteration. DEBUG with sample, or a single INFO with batch summary.",
     "tags": ["ops", "logging", "perf"]},

    # ── ML (9) ──────────────────────────────────────────────────────────────
    {"id": "ml01", "type": "pattern", "maturity": "canon",
     "title": "Set random seeds for reproducibility",
     "content": "torch.manual_seed + np.random.seed + random.seed + PYTHONHASHSEED. Forgetting any one breaks reproducibility; pin them in train and eval entrypoints.",
     "tags": ["ml", "reproducibility"]},
    {"id": "ml02", "type": "lesson", "maturity": "validated",
     "title": "Embedding model cold-start dominates first-query latency",
     "content": "MiniLM loads in 2-3s. Load eagerly at process start; do not lazy-load on first request. p99 will hide it; the first user feels it every restart.",
     "tags": ["ml", "embeddings", "perf"]},
    {"id": "ml03", "type": "pattern", "maturity": "validated",
     "title": "RAG: hybrid search beats pure vector",
     "content": "BM25 + vector via RRF (k=40-60) outperforms either alone on real corpora by 5-15 nDCG points. Lexical recall covers vector's blind spots.",
     "tags": ["ml", "rag", "search"]},
    {"id": "ml04", "type": "pattern", "maturity": "validated",
     "title": "Eval set frozen before model selection",
     "content": "If you tune on the eval set you're training on it. Hold out a frozen eval; only touch when comparing finalists.",
     "tags": ["ml", "eval", "methodology"]},
    {"id": "ml05", "type": "anti_pattern", "maturity": "validated",
     "title": "Fine-tuning on small data without held-out eval",
     "content": "100 examples + LoRA looks great on the training prompt and falls apart in the wild. Always have a held-out eval and a regression suite.",
     "tags": ["ml", "fine-tune", "eval"]},
    {"id": "ml06", "type": "pattern", "maturity": "tested",
     "title": "Prompt templates versioned in code, not hard-coded",
     "content": "Treat prompts as code: version control, code review, A/B-able. Tracks which prompt produced which eval score.",
     "tags": ["ml", "prompt", "versioning"]},
    {"id": "ml07", "type": "lesson", "maturity": "validated",
     "title": "Retrieval quality bounds RAG quality",
     "content": "If the right doc isn't in the top-k, the LLM can't use it. Invest in retrieval evaluation (nDCG, recall@k) before tuning generation prompts.",
     "tags": ["ml", "rag", "retrieval"]},
    {"id": "ml08", "type": "pattern", "maturity": "validated",
     "title": "Cache LLM responses by prompt hash for idempotent prompts",
     "content": "Deterministic prompts (temp=0) hit the cache; cuts cost and latency. Invalidate on prompt or model version change.",
     "tags": ["ml", "cache", "cost"]},
    {"id": "ml09", "type": "observation", "maturity": "tested",
     "title": "Embedding drift between model versions",
     "content": "all-MiniLM-L6-v2 vs all-mpnet-base-v2 produce non-comparable spaces. Re-embed the entire corpus on model upgrade; never mix.",
     "tags": ["ml", "embeddings", "versioning"]},

    # ── Architecture decisions (11) ─────────────────────────────────────────
    {"id": "arch01", "type": "decision", "maturity": "canon",
     "title": "FastAPI + Pydantic v2 + SQLAlchemy 2 as default Python web stack",
     "content": "Async-native, typed end-to-end, fast. Beats Flask + Marshmallow on perf and ergonomics. 3 production services migrated; no regrets.",
     "tags": ["architecture", "python", "stack"]},
    {"id": "arch02", "type": "decision", "maturity": "validated",
     "title": "Monorepo with workspaces for tightly-coupled services",
     "content": "Single PR can land changes across web + worker + shared lib. Polyrepo overhead (versioning, CI fan-out) wasn't worth it under 30 services.",
     "tags": ["architecture", "monorepo"]},
    {"id": "arch03", "type": "decision", "maturity": "validated",
     "title": "Event-driven for cross-service consistency",
     "content": "Outbox pattern + Kafka for things that span service boundaries. Synchronous chains create tight coupling and cascading failures.",
     "tags": ["architecture", "events", "kafka"]},
    {"id": "arch04", "type": "decision", "maturity": "tested",
     "title": "Use SQLite for local-first apps, sync via CRDT later",
     "content": "Single-user desktop / CLI tools ship faster on SQLite. Add sync only when multi-device demands it; CRDTs (Yjs, Automerge) handle the merge.",
     "tags": ["architecture", "sqlite", "local-first"]},
    {"id": "arch05", "type": "decision", "maturity": "validated",
     "title": "Boring tech for the data plane",
     "content": "Postgres + Redis + a queue covers 95% of needs. Fancy data store = an on-call surface area you'll regret at 3am.",
     "tags": ["architecture", "data", "decision"]},
    {"id": "arch06", "type": "decision", "maturity": "tested",
     "title": "TypeScript + Next.js for new web frontends",
     "content": "Types catch refactor bugs; Next gives you SSR, RSC, and a deploy story out of the box. Vite/Remix valid alternates if SSR isn't needed.",
     "tags": ["architecture", "frontend", "stack"]},
    {"id": "arch07", "type": "decision", "maturity": "validated",
     "title": "Adopt OpenTelemetry over vendor-specific tracing",
     "content": "OTel is the industry default; APM vendors all ingest it. Avoids lock-in; lets us swap backends (Honeycomb / Datadog / Tempo) without re-instrumenting.",
     "tags": ["architecture", "tracing", "otel"]},
    {"id": "arch08", "type": "decision", "maturity": "tested",
     "title": "Keep state in Postgres until you measurably outgrow it",
     "content": "Specialty stores (Mongo, Cassandra) impose ops cost from day one. Postgres at 1 TB with the right indexes is faster than most alternatives.",
     "tags": ["architecture", "postgres", "scale"]},
    {"id": "arch09", "type": "decision", "maturity": "validated",
     "title": "Synchronous vs async per workload, not per service",
     "content": "Same FastAPI process can serve sync DB reads and async fan-out endpoints. Picking one religiously costs ergonomics on the wrong workloads.",
     "tags": ["architecture", "async", "fastapi"]},
    {"id": "arch10", "type": "decision", "maturity": "validated",
     "title": "Choose RRF over linear blend for hybrid retrieval",
     "content": "Reciprocal Rank Fusion (Cormack 2009) avoids score-scale calibration between BM25 and cosine. k=40-60 robust on small corpora; +3-5 nDCG vs blend.",
     "tags": ["architecture", "search", "rrf"]},
    {"id": "arch11", "type": "decision", "maturity": "tested",
     "title": "Server-sent events over WebSockets for one-way streams",
     "content": "SSE works through proxies, auto-reconnects, and is plain HTTP. Use WebSockets only when you actually need bidirectional or binary frames.",
     "tags": ["architecture", "streaming", "sse"]},

    # ── Lessons / postmortems (9) ───────────────────────────────────────────
    {"id": "less01", "type": "lesson", "maturity": "canon",
     "title": "Default values in shared mutable args bite hard",
     "content": "def f(x=[]) shares the list across calls. Use None and create inside. Caused a multi-tenant data leak in week 3 of production.",
     "tags": ["python", "lesson", "bug"]},
    {"id": "less02", "type": "lesson", "maturity": "validated",
     "title": "Migrations that lock big tables need a maintenance window",
     "content": "ALTER TABLE on a 100 GB table held an exclusive lock for 12 minutes. Use online schema change tools (gh-ost, pt-osc) or chunked backfills.",
     "tags": ["database", "migration", "lesson"]},
    {"id": "less03", "type": "lesson", "maturity": "validated",
     "title": "Cron jobs without monitoring become silently broken",
     "content": "A nightly billing job stopped emailing reports for 6 weeks because cron stderr went to /dev/null. Healthchecks.io / dead-man's-switch on every cron.",
     "tags": ["ops", "cron", "monitoring"]},
    {"id": "less04", "type": "lesson", "maturity": "validated",
     "title": "Soft-delete fields drift from queries",
     "content": "Half the queries forgot WHERE deleted_at IS NULL. Use a row-level policy or always-applied scope; better, use real delete + archive table.",
     "tags": ["database", "soft-delete", "lesson"]},
    {"id": "less05", "type": "lesson", "maturity": "validated",
     "title": "Daylight saving stored as local time corrupts data",
     "content": "Logs at 02:30 local appear twice (or zero times) on DST switch days. Always store UTC, render local on display.",
     "tags": ["time", "timezone", "lesson"]},
    {"id": "less06", "type": "lesson", "maturity": "validated",
     "title": "Float for money creates rounding incidents",
     "content": "0.1 + 0.2 ≠ 0.3. Use integer cents or Decimal. Discovered after a customer-facing total mismatch on a $1M invoice.",
     "tags": ["money", "decimal", "lesson"]},
    {"id": "less07", "type": "lesson", "maturity": "validated",
     "title": "Background jobs on the web process starve requests",
     "content": "Sync background work in the request handler ate one of two workers under load. Move to a queue worker (RQ, Celery, Arq).",
     "tags": ["perf", "queue", "lesson"]},
    {"id": "less08", "type": "lesson", "maturity": "tested",
     "title": "Feature flag service is now a tier-0 dependency",
     "content": "When LD/Flagsmith goes down, default-deny means features stop working. Cache flags with stale-on-error; default to last-known.",
     "tags": ["ops", "feature-flags", "lesson"]},
    {"id": "less09", "type": "lesson", "maturity": "tested",
     "title": "Dependency upgrades batched monthly, not yearly",
     "content": "A 12-month gap on a major framework = days of work; monthly = an hour. Schedule a recurring upgrade sprint; renovate-bot opens the PRs.",
     "tags": ["deps", "upgrade", "lesson"]},

    # ── Observations (3) ────────────────────────────────────────────────────
    {"id": "obs01", "type": "observation", "maturity": "tested",
     "title": "Most outages stem from configuration changes, not code",
     "content": "60-70% per public post-mortems (AWS, GitHub, Cloudflare). Config changes need the same review and canary as code; don't trust hot-reload paths.",
     "tags": ["ops", "config", "outage"]},
    {"id": "obs02", "type": "observation", "maturity": "validated",
     "title": "p99 latency tracks GC pauses on JVM workloads",
     "content": "Allocator pressure → frequent young-gen GCs → multi-second p99 spikes. ZGC / Shenandoah cuts the tail; allocate-once patterns help more.",
     "tags": ["perf", "jvm", "gc"]},
    {"id": "obs03", "type": "observation", "maturity": "hypothesis",
     "title": "Vector-only retrieval underperforms hybrid on short queries",
     "content": "Anecdotal: 3-token queries lose to BM25. Vectors over-smooth; lexical exact match wins. Confirm with a proper bench on internal corpus.",
     "tags": ["ml", "retrieval", "hypothesis"]},

    # ── Filler patterns to balance type/maturity distribution ──────────────
    # These are real patterns we'd record; they exist primarily to push the
    # type distribution closer to the ~70 % pattern target and to widen the
    # canon tier so the maturity_bias@5 metric has signal.
    {"id": "test15", "type": "pattern", "maturity": "canon",
     "title": "Run tests in parallel with pytest-xdist",
     "content": "pytest -n auto runs tests in parallel across cores. Cuts a 10-minute suite to 2-3 min on a modern laptop. Watch for shared-state hazards.",
     "tags": ["testing", "pytest", "parallel"]},
    {"id": "db18", "type": "pattern", "maturity": "canon",
     "title": "Index foreign-key columns by default",
     "content": "Postgres does NOT auto-index FKs (unlike MySQL). Joins on unindexed FKs do sequential scans; ON DELETE CASCADE is also slow without the index.",
     "tags": ["database", "index", "postgres"]},
    {"id": "db19", "type": "pattern", "maturity": "canon",
     "title": "Use UUIDv7 over UUIDv4 for primary keys",
     "content": "v7 is timestamp-prefixed → index locality, predictable B-tree growth, no hot-page contention. v4 random scatters writes across the index.",
     "tags": ["database", "uuid", "index"]},
    {"id": "api18", "type": "pattern", "maturity": "canon",
     "title": "Return Problem Details (RFC 7807) for API errors",
     "content": "Standardized error envelope (type, title, status, detail, instance). Clients parse one shape; debugging gets a stable contract across services.",
     "tags": ["api", "errors", "rest"]},
    {"id": "api19", "type": "pattern", "maturity": "canon",
     "title": "Validate Content-Type before parsing the body",
     "content": "Reject early if the client sent text/plain when you expected JSON. 415 Unsupported Media Type beats a parser stack trace and stops scrapers cheaply.",
     "tags": ["api", "validation", "rest"]},
    {"id": "sec17", "type": "pattern", "maturity": "canon",
     "title": "Apply CSRF protection on every state-changing endpoint",
     "content": "Cookie-based sessions need CSRF tokens (double-submit or SameSite=Lax). Bearer-token APIs are immune. Forgetting on one mutation is enough for a CSRF.",
     "tags": ["security", "csrf", "auth"]},
    {"id": "sec18", "type": "pattern", "maturity": "canon",
     "title": "Use Content-Security-Policy with strict default-src 'self'",
     "content": "Default-deny inline + cross-origin scripts; allow nonced inline only. Catches XSS at the browser even when input sanitization slips.",
     "tags": ["security", "csp", "xss"]},
    {"id": "perf14", "type": "pattern", "maturity": "canon",
     "title": "Use HTTP/2 or HTTP/3 for multiplexed clients",
     "content": "Single connection, header compression, no head-of-line blocking. Most server frameworks default to HTTP/2 behind TLS now; flip the switch.",
     "tags": ["perf", "http", "network"]},
    {"id": "fe14", "type": "pattern", "maturity": "canon",
     "title": "Stable component keys in lists",
     "content": "key={item.id}, never key={index}. Index keys cause focus loss, lost input state, and animation tearing on reorder.",
     "tags": ["frontend", "react", "lists"]},
    {"id": "ops14", "type": "pattern", "maturity": "canon",
     "title": "Pin Docker base images by digest",
     "content": "FROM python:3.12 silently floats; FROM python:3.12@sha256:... is reproducible. Renovate-bot opens upgrade PRs instead of mystery breakage.",
     "tags": ["ops", "docker", "supply-chain"]},
    {"id": "ops15", "type": "pattern", "maturity": "canon",
     "title": "Alert on symptoms, not causes",
     "content": "Page on 'checkout p99 > 1s', not 'CPU > 80%'. Cause-based alerts fire on benign noise; symptom alerts fire when users hurt.",
     "tags": ["ops", "alerts", "slo"]},
    {"id": "ml10", "type": "pattern", "maturity": "canon",
     "title": "Chunk long documents at semantic boundaries before embedding",
     "content": "Splitting at sentence/paragraph beats fixed-token chunks. Recursive splitter with overlap (50-100 tokens) is the practical default.",
     "tags": ["ml", "rag", "chunking"]},

    # ── R12 expansion (108 entries, prefix r12_*) ───────────────────────────
    # Each new memory exists to support a specific cluster of new queries:
    #   r12_cs_*  → code_specific (rare identifiers, lexically discoverable)
    #   r12_ap_*  → anti_pattern_intent (severity=high/critical antipatterns)
    #   r12_on_*  → onboarding_to_stack (canon-tier onboarding for one stack)
    #   r12_dr_*  → diff_review (anti_pattern matched against pasted diff)
    #   r12_ml_*  → multilingual_lite (CS/DE content paired with EN siblings)
    #   r12_lg_*  → lexical_gap_hard (no token overlap with paraphrastic query)

    # ── code_specific (rare identifiers, ~20 entries) ──────────────────────
    {"id": "r12_cs_pgvector_hnsw", "type": "pattern", "maturity": "canon",
     "title": "Configure pgvector HNSW index for sub-50ms ANN at 1M rows",
     "content": "CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64). Query with SET hnsw.ef_search=40. Beats ivfflat on recall above 100K rows.",
     "tags": ["pgvector", "postgres", "hnsw", "vector"]},
    {"id": "r12_cs_sqlite_vec_serialize", "type": "pattern", "maturity": "validated",
     "title": "sqlite-vec serialize_float32 round-trip for embeddings",
     "content": "Use sqlite_vec.serialize_float32(vec) on insert; the virtual table stores packed bytes. Reading back via SELECT vec_to_json keeps Python types stable.",
     "tags": ["sqlite-vec", "embeddings", "sqlite"]},
    {"id": "r12_cs_argon2_params_2025", "type": "pattern", "maturity": "canon",
     "title": "Argon2id parameters for OWASP 2025: t=3, m=12 MiB, p=1",
     "content": "OWASP raised the floor: time_cost=3, memory_cost=12288 (KiB), parallelism=1. argon2-cffi PasswordHasher(time_cost=3, memory_cost=12288).",
     "tags": ["argon2", "argon2id", "password", "security"]},
    {"id": "r12_cs_uvicorn_workers", "type": "pattern", "maturity": "validated",
     "title": "uvicorn --workers count vs gunicorn -k uvicorn.workers.UvicornWorker",
     "content": "uvicorn's built-in --workers fork is fine for dev; in prod use gunicorn with the UvicornWorker class so you get graceful restarts, timeouts, and process supervision.",
     "tags": ["uvicorn", "gunicorn", "fastapi", "deploy"]},
    {"id": "r12_cs_alembic_autogen", "type": "pattern", "maturity": "validated",
     "title": "alembic revision --autogenerate caveats for SQLite",
     "content": "Autogenerate misses CHECK constraints and partial indexes on SQLite. Always review the generated migration; add op.batch_alter_table for column type changes.",
     "tags": ["alembic", "sqlalchemy", "migrations", "sqlite"]},
    {"id": "r12_cs_pytest_parametrize_ids", "type": "pattern", "maturity": "validated",
     "title": "pytest.mark.parametrize with explicit ids for readable failures",
     "content": "@pytest.mark.parametrize('case', cases, ids=lambda c: c.name) makes the failure header useful. Default ids stringify the value and become noise on dataclasses.",
     "tags": ["pytest", "parametrize", "testing"]},
    {"id": "r12_cs_pytest_asyncio_strict", "type": "pattern", "maturity": "validated",
     "title": "pytest-asyncio strict mode disables auto event-loop coercion",
     "content": "Set asyncio_mode = strict in pyproject; mark every coroutine test with @pytest.mark.asyncio. Auto mode hides forgotten markers and silently passes sync tests.",
     "tags": ["pytest", "pytest-asyncio", "async"]},
    {"id": "r12_cs_orjson_default", "type": "pattern", "maturity": "validated",
     "title": "orjson with option=ORJSON_OPT_SERIALIZE_NUMPY for arrays",
     "content": "orjson.dumps(arr, option=orjson.OPT_SERIALIZE_NUMPY) bypasses .tolist() roundtrips; 5-10x faster than stdlib json on numpy-heavy responses.",
     "tags": ["orjson", "json", "numpy", "perf"]},
    {"id": "r12_cs_httpx_async_client", "type": "pattern", "maturity": "validated",
     "title": "httpx.AsyncClient lifespan tied to the FastAPI app",
     "content": "Create httpx.AsyncClient on startup, close on shutdown. One pool per process beats per-request clients; pair with a sane Limits(max_connections=100).",
     "tags": ["httpx", "async", "fastapi", "http"]},
    {"id": "r12_cs_celery_acks_late", "type": "pattern", "maturity": "validated",
     "title": "Celery acks_late=True with worker_prefetch_multiplier=1",
     "content": "Default acks_late=False loses tasks on worker crash mid-task. acks_late + prefetch=1 = at-least-once delivery; pair with idempotent task design.",
     "tags": ["celery", "queue", "reliability"]},
    {"id": "r12_cs_redis_setnx_lock", "type": "pattern", "maturity": "validated",
     "title": "Use SET NX EX for distributed locks, not SETNX + EXPIRE",
     "content": "SET key val NX EX 30 is atomic; SETNX followed by EXPIRE leaves a permanent lock if the client crashes between calls. Always use the unified SET form.",
     "tags": ["redis", "lock", "distributed"]},
    {"id": "r12_cs_psycopg3_pipeline", "type": "pattern", "maturity": "tested",
     "title": "psycopg3 pipeline mode batches round trips",
     "content": "with conn.pipeline(): execute many writes in one network turn. Cuts insert latency on RTT-bound workloads (e.g. cross-region) by 3-5x.",
     "tags": ["psycopg3", "postgres", "perf"]},
    {"id": "r12_cs_ruff_select", "type": "pattern", "maturity": "validated",
     "title": "ruff select = ['E','F','W','I','UP','B','SIM'] minimum",
     "content": "Pyflakes + pycodestyle + isort + pyupgrade + bugbear + flake8-simplify cover 90% of issues without flake8's per-file plugin tax. Add 'D' for docstrings on libraries.",
     "tags": ["ruff", "lint", "python"]},
    {"id": "r12_cs_mypy_strict", "type": "pattern", "maturity": "validated",
     "title": "mypy --strict per package, not project-wide",
     "content": "Project-wide --strict on a legacy codebase produces 4000 errors. Enable per-package via [[tool.mypy.overrides]]; ratchet outward as packages clean up.",
     "tags": ["mypy", "typing", "python"]},
    {"id": "r12_cs_react_hook_deps", "type": "pattern", "maturity": "canon",
     "title": "ESLint react-hooks/exhaustive-deps as error, not warning",
     "content": "Default rule level is warn; flip to error in CI. Stale closures in useEffect/useCallback are the #1 source of subtle re-render bugs.",
     "tags": ["react", "useEffect", "eslint", "frontend"]},
    {"id": "r12_cs_tanstack_query_keys", "type": "pattern", "maturity": "validated",
     "title": "TanStack Query: structured query keys + invalidateQueries",
     "content": "['users', userId, 'posts'] beats 'users-N-posts'. Lets invalidateQueries(['users']) cascade. Co-locate the keyFactory next to the hook.",
     "tags": ["tanstack-query", "react-query", "react", "state"]},
    {"id": "r12_cs_otel_python_auto", "type": "pattern", "maturity": "validated",
     "title": "opentelemetry-instrument auto-instrumentation entry point",
     "content": "opentelemetry-instrument python -m yourapp wraps requests, sqlalchemy, fastapi without code changes. Configure exporter via OTEL_EXPORTER_OTLP_ENDPOINT.",
     "tags": ["opentelemetry", "otel", "tracing", "python"]},
    {"id": "r12_cs_pgbouncer_transaction", "type": "pattern", "maturity": "validated",
     "title": "PgBouncer transaction-pool mode + DISABLE prepared statements",
     "content": "pool_mode=transaction multiplexes clients onto fewer backend connections. SQLAlchemy: connect_args={'prepare_threshold': None} or fail with 'prepared statement does not exist'.",
     "tags": ["pgbouncer", "postgres", "pool"]},
    {"id": "r12_cs_systemd_unit_restart", "type": "pattern", "maturity": "validated",
     "title": "systemd Restart=on-failure with RestartSec + StartLimitBurst",
     "content": "Restart=on-failure alone loops forever on a config bug. Pair with StartLimitBurst=5 + StartLimitIntervalSec=60 so systemd gives up rather than thrashing.",
     "tags": ["systemd", "ops", "linux"]},
    {"id": "r12_cs_terraform_lifecycle", "type": "pattern", "maturity": "validated",
     "title": "Terraform lifecycle prevent_destroy on stateful resources",
     "content": "RDS, S3 buckets with data, DNS zones: lifecycle { prevent_destroy = true }. Forces an explicit removal of the block before destroy; saves you from a careless apply.",
     "tags": ["terraform", "iac", "ops"]},

    # ── anti_pattern_intent (severity=high/critical, ~22 entries) ─────────
    {"id": "r12_ap_rsa_key_in_env", "type": "anti_pattern", "maturity": "canon",
     "title": "Storing RSA private keys in plain environment variables",
     "content": "Env vars leak via /proc, crash dumps, and process listings. Mount a tmpfs file or use the cloud KMS; rotate the key if it ever sat in env.",
     "tags": ["security", "secrets", "rsa", "key-management"]},
    {"id": "r12_ap_unverified_webhooks", "type": "anti_pattern", "maturity": "canon",
     "title": "Accepting webhook payloads without HMAC signature verification",
     "content": "Anyone can POST to your webhook URL. Verify the provider's HMAC header (Stripe-Signature, X-Hub-Signature-256) with constant-time compare before processing.",
     "tags": ["security", "webhook", "hmac", "verification"]},
    {"id": "r12_ap_pickle_untrusted", "type": "anti_pattern", "maturity": "canon",
     "title": "pickle.loads on untrusted input is RCE",
     "content": "pickle is not a serialization format — it's a deserialization-side eval. For cross-process data use msgpack/json; for ML models load from a trusted artifact registry.",
     "tags": ["security", "pickle", "rce", "python"]},
    {"id": "r12_ap_yaml_load_unsafe", "type": "anti_pattern", "maturity": "canon",
     "title": "yaml.load without SafeLoader instantiates arbitrary Python",
     "content": "yaml.load(s) honours !!python/object tags → RCE. Use yaml.safe_load or yaml.load(s, Loader=SafeLoader). Default Loader was deprecated for this exact reason.",
     "tags": ["security", "yaml", "rce", "python"]},
    {"id": "r12_ap_xxe_xml", "type": "anti_pattern", "maturity": "validated",
     "title": "XML parsing without disabling external entities (XXE)",
     "content": "lxml's default resolves external entities → file read / SSRF. Use defusedxml or set resolve_entities=False, no_network=True on the parser.",
     "tags": ["security", "xxe", "xml", "ssrf"]},
    {"id": "r12_ap_open_redirect", "type": "anti_pattern", "maturity": "validated",
     "title": "Open redirect via unchecked ?next= parameter",
     "content": "redirect(request.GET['next']) lets phishers craft links from your domain to theirs. Allowlist hostnames (or paths only) before the redirect.",
     "tags": ["security", "open-redirect", "phishing", "web"]},
    {"id": "r12_ap_ssrf_url_fetch", "type": "anti_pattern", "maturity": "validated",
     "title": "Fetching arbitrary user URLs without SSRF guards",
     "content": "Server-side fetch on a user-supplied URL hits cloud metadata (169.254.169.254), internal services, and link-local addresses. Resolve + reject private/loopback before the call.",
     "tags": ["security", "ssrf", "url-fetch"]},
    {"id": "r12_ap_timing_unsafe_compare", "type": "anti_pattern", "maturity": "validated",
     "title": "Comparing tokens with == leaks bytes via timing",
     "content": "Python str.__eq__ short-circuits on first mismatch. Use hmac.compare_digest for token/HMAC equality so attackers can't byte-by-byte recover the secret.",
     "tags": ["security", "timing", "hmac", "python"]},
    {"id": "r12_ap_path_traversal", "type": "anti_pattern", "maturity": "canon",
     "title": "Path traversal via concatenated user input",
     "content": "open(BASE + user_path) lets ../../etc/passwd through. Resolve to canonical path, then assert it's under BASE; pathlib.Path.resolve() + relative_to() catches it.",
     "tags": ["security", "path-traversal", "filesystem"]},
    {"id": "r12_ap_cors_star_creds", "type": "anti_pattern", "maturity": "canon",
     "title": "Access-Control-Allow-Origin: * with credentials enabled",
     "content": "Browsers block * + credentials, but a misconfigured proxy can rewrite to a single origin and ship cookies. Always echo a vetted origin, never *, when credentials=true.",
     "tags": ["security", "cors", "credentials", "browser"]},
    {"id": "r12_ap_jwt_secret_in_repo", "type": "anti_pattern", "maturity": "canon",
     "title": "Committing a JWT signing secret to the repository",
     "content": "git history is forever. Rotate the secret immediately, audit issued tokens, and add gitleaks/trufflehog to pre-commit so the next one is caught at staging time.",
     "tags": ["security", "jwt", "secrets", "git"]},
    {"id": "r12_ap_disable_csrf_for_api", "type": "anti_pattern", "maturity": "validated",
     "title": "Disabling CSRF middleware globally because the API is bearer-auth",
     "content": "Mixed cookie+bearer apps still need CSRF on cookie-auth endpoints. Disable per-route, never globally; use a CSRF middleware that exempts known bearer routes.",
     "tags": ["security", "csrf", "middleware"]},
    {"id": "r12_ap_mass_assign", "type": "anti_pattern", "maturity": "validated",
     "title": "Mass-assigning request body straight to ORM model",
     "content": "User(**request.json) lets the client set is_admin=true. Project an explicit Pydantic schema; ORM model is the storage shape, not the API contract.",
     "tags": ["security", "mass-assignment", "orm"]},
    {"id": "r12_ap_log_pii", "type": "anti_pattern", "maturity": "validated",
     "title": "Logging full user objects (including PII) at INFO",
     "content": "User pydantic model in the log body = email/phone/SSN in your log store. Use a structlog processor or pydantic SecretStr; redact before serialization.",
     "tags": ["security", "logging", "pii", "gdpr"]},
    {"id": "r12_ap_dependency_pin_caret", "type": "anti_pattern", "maturity": "validated",
     "title": "Caret-ranges (^1.2.3) in production lockfiles",
     "content": "^ allows minor upgrades; a malicious or buggy minor lands in your image at next build. Lockfile + exact pin in package.json, Renovate-bot for upgrades.",
     "tags": ["security", "dependencies", "supply-chain"]},
    {"id": "r12_ap_unbounded_recursion", "type": "anti_pattern", "maturity": "validated",
     "title": "Unbounded recursion in tree/graph traversal",
     "content": "Crafted nesting blows the stack (1000-deep). Convert to iterative + explicit stack, or impose a max-depth and reject early. Same applies to JSON parsers.",
     "tags": ["security", "dos", "recursion"]},
    {"id": "r12_ap_regex_redos", "type": "anti_pattern", "maturity": "validated",
     "title": "Regex with catastrophic backtracking (ReDoS)",
     "content": "(a+)+$ on long strings hangs a thread. Use re2 (no backtracking), bound input length, or write a deterministic regex; lint with re-redos.",
     "tags": ["security", "regex", "redos", "dos"]},
    {"id": "r12_ap_sleep_in_request", "type": "anti_pattern", "maturity": "validated",
     "title": "time.sleep() inside a sync request handler",
     "content": "Blocks the worker thread; one slow client starves all others. Use asyncio.sleep in async handlers, or move retry/backoff into a background task.",
     "tags": ["perf", "async", "blocking"]},
    {"id": "r12_ap_swallow_exceptions", "type": "anti_pattern", "maturity": "validated",
     "title": "except Exception: pass swallowing all errors silently",
     "content": "Hides the bug, breaks logging, makes the on-call hate you. Catch the specific exception class; log + re-raise if you can't handle it.",
     "tags": ["python", "exceptions", "anti-pattern"]},
    {"id": "r12_ap_global_db_session", "type": "anti_pattern", "maturity": "validated",
     "title": "Module-level DB session shared across threads",
     "content": "SQLAlchemy Session is not thread-safe. Use scoped_session + request-scoped lifecycle (FastAPI Depends, Flask before_request).",
     "tags": ["database", "sqlalchemy", "threading"]},
    {"id": "r12_ap_no_ttl_redis", "type": "anti_pattern", "maturity": "validated",
     "title": "Writing to Redis without setting EX or PX",
     "content": "Cache without a TTL becomes a database with no schema and no eviction. Always set EX on writes; pair with maxmemory-policy allkeys-lru as a safety net.",
     "tags": ["redis", "cache", "ttl"]},
    {"id": "r12_ap_kubectl_apply_in_ci", "type": "anti_pattern", "maturity": "validated",
     "title": "kubectl apply -f from CI without diff/dry-run gate",
     "content": "Surprises in dev manifests land in prod. kubectl diff in PR, kubectl apply --server-side --dry-run=server in CI, then apply only on protected branches.",
     "tags": ["kubernetes", "ops", "ci"]},

    # ── onboarding_to_stack (canon-tier per stack, ~14 entries) ───────────
    {"id": "r12_on_postgres_first", "type": "pattern", "maturity": "canon",
     "title": "Postgres essentials for new developers: indexes, EXPLAIN, transactions",
     "content": "Read EXPLAIN ANALYZE output. Use composite indexes on (filter, sort) cols. Wrap multi-statement work in transactions. Avoid SELECT *. These four cover 80% of DB issues.",
     "tags": ["postgres", "onboarding", "database"]},
    {"id": "r12_on_postgres_locks", "type": "pattern", "maturity": "canon",
     "title": "Postgres lock awareness for newcomers",
     "content": "ALTER TABLE on a busy table queues every read. Use SET lock_timeout, run DDL in maintenance windows, and prefer ADD COLUMN ... NULL over ADD COLUMN ... NOT NULL DEFAULT.",
     "tags": ["postgres", "onboarding", "locks"]},
    {"id": "r12_on_fastapi_basics", "type": "pattern", "maturity": "canon",
     "title": "FastAPI essentials: dependency injection, Pydantic, lifespan",
     "content": "Use Depends for shared resources (DB session, http client). Pydantic models at request and response boundary. Async lifespan for startup/shutdown; never sleep in handlers.",
     "tags": ["fastapi", "onboarding", "python"]},
    {"id": "r12_on_fastapi_testing", "type": "pattern", "maturity": "canon",
     "title": "FastAPI testing essentials: TestClient + dependency override",
     "content": "Use TestClient(app) for end-to-end. app.dependency_overrides swaps DB/http for fakes. Pair with httpx.AsyncClient + ASGITransport for async paths.",
     "tags": ["fastapi", "onboarding", "testing"]},
    {"id": "r12_on_react_first", "type": "pattern", "maturity": "canon",
     "title": "React essentials for new developers: hooks, keys, server state",
     "content": "useState for local UI, TanStack Query for server state, key={item.id} for lists, useEffect with full dep array. Memoize only when profiler shows a hot render.",
     "tags": ["react", "onboarding", "frontend"]},
    {"id": "r12_on_nextjs_first", "type": "pattern", "maturity": "canon",
     "title": "Next.js App Router essentials: server vs client components",
     "content": "Default = server component (no JS shipped). Add 'use client' only for interactivity. Co-locate fetch in the server component; pass typed props to the client island.",
     "tags": ["nextjs", "onboarding", "react"]},
    {"id": "r12_on_tailwind_first", "type": "pattern", "maturity": "canon",
     "title": "Tailwind essentials: utility-first, design tokens, JIT",
     "content": "Edit tailwind.config.{theme.extend} for tokens (colours, spacing). Use @apply sparingly — it defeats co-location. JIT only ships classes you wrote.",
     "tags": ["tailwind", "onboarding", "css"]},
    {"id": "r12_on_argon2_first", "type": "pattern", "maturity": "canon",
     "title": "Argon2id essentials: parameters, library, rotation",
     "content": "Use argon2-cffi (Python) / @node-rs/argon2 (Node). PasswordHasher() defaults are sane in 2025. Re-hash on login if check_needs_rehash returns True; lets you raise costs cheaply.",
     "tags": ["argon2", "onboarding", "security", "password"]},
    {"id": "r12_on_sqlalchemy_first", "type": "pattern", "maturity": "canon",
     "title": "SQLAlchemy 2.0 essentials: declarative, scoped sessions, eager-load",
     "content": "Mapped[T] declarative, session per request, selectinload for collections, joinedload for one-to-one. Avoid lazy='dynamic' — it surprises new readers.",
     "tags": ["sqlalchemy", "onboarding", "python", "database"]},
    {"id": "r12_on_pydantic_first", "type": "pattern", "maturity": "canon",
     "title": "Pydantic v2 essentials: BaseModel, Field, validators, settings",
     "content": "BaseModel for data, BaseSettings for env. Field(default_factory=list) avoids the mutable-default trap. @field_validator runs after type coercion; @model_validator after the whole model.",
     "tags": ["pydantic", "onboarding", "python", "validation"]},
    {"id": "r12_on_docker_first", "type": "pattern", "maturity": "canon",
     "title": "Docker essentials for new developers: layers, build cache, slim images",
     "content": "Order Dockerfile from least to most volatile. Multi-stage builds for compiled deps. Pin base image by digest. Run as nonroot. .dockerignore mirrors .gitignore.",
     "tags": ["docker", "onboarding", "ops"]},
    {"id": "r12_on_kubernetes_first", "type": "pattern", "maturity": "canon",
     "title": "Kubernetes essentials for new operators: probes, requests, secrets",
     "content": "readinessProbe = serve traffic, livenessProbe = restart. Always set requests + limits. Mount secrets via projected volume; never bake into the image.",
     "tags": ["kubernetes", "onboarding", "ops"]},
    {"id": "r12_on_otel_first", "type": "pattern", "maturity": "canon",
     "title": "OpenTelemetry essentials: traces, metrics, baggage, exporters",
     "content": "Auto-instrument first; add manual spans only on business-critical work. W3C traceparent across services. OTLP to a vendor-agnostic backend; switch backends without code change.",
     "tags": ["opentelemetry", "otel", "onboarding", "tracing"]},
    {"id": "r12_on_terraform_first", "type": "pattern", "maturity": "canon",
     "title": "Terraform essentials: state, modules, lifecycle, plan/apply gates",
     "content": "Remote state with locking (S3+DynamoDB or Terraform Cloud). One module per concern. lifecycle { prevent_destroy } on stateful. plan in PR, apply behind manual approval.",
     "tags": ["terraform", "onboarding", "iac", "ops"]},

    # ── diff_review (anti-patterns matched against pasted diff, ~16) ──────
    {"id": "r12_dr_requests_no_timeout", "type": "anti_pattern", "maturity": "canon",
     "title": "requests.get/post called without timeout in diff",
     "content": "+ requests.get(url) / + requests.post(url, json=...) — every PR review must flag missing timeout=. Default to (3.05, 10) connect/read tuple.",
     "tags": ["python", "requests", "http", "review"]},
    {"id": "r12_dr_print_in_handler", "type": "anti_pattern", "maturity": "validated",
     "title": "print() statements left in request handlers",
     "content": "print writes to stdout; in a structured-log world it's invisible to your aggregator and mixes with framework output. Use logger; lint with no-print rule.",
     "tags": ["python", "logging", "review"]},
    {"id": "r12_dr_assert_for_validation", "type": "anti_pattern", "maturity": "validated",
     "title": "assert used for input validation in production code",
     "content": "python -O strips asserts. assert user.is_authenticated becomes a no-op in optimized builds. Use explicit if + raise.",
     "tags": ["python", "assert", "validation", "review"]},
    {"id": "r12_dr_subprocess_shell_true", "type": "anti_pattern", "maturity": "canon",
     "title": "subprocess.run(..., shell=True) with interpolated input",
     "content": "subprocess.run(f'rm -rf {path}', shell=True) is shell injection. Pass a list, shell=False (default). Use shlex.quote only when shell is unavoidable.",
     "tags": ["python", "subprocess", "shell-injection", "security"]},
    {"id": "r12_dr_str_format_sql", "type": "anti_pattern", "maturity": "canon",
     "title": "Building SQL with str.format / f-string in diff",
     "content": "+ f\"SELECT * FROM users WHERE id = {uid}\" — SQL injection. Use parameterized queries; ORM bind params; psycopg %s placeholders.",
     "tags": ["sql", "injection", "security", "review"]},
    {"id": "r12_dr_md5_password", "type": "anti_pattern", "maturity": "canon",
     "title": "hashlib.md5(password) or sha1(password) for hashing passwords",
     "content": "MD5/SHA1 are GPU-fast → rainbow tables / brute force. Use argon2id. If you saw md5/sha1 in a password path, the diff is a rejection.",
     "tags": ["crypto", "password", "md5", "sha1", "security"]},
    {"id": "r12_dr_pickle_in_diff", "type": "anti_pattern", "maturity": "canon",
     "title": "pickle.loads on user-supplied data in the diff",
     "content": "+ data = pickle.loads(request.body) — RCE. Replace with json.loads for arbitrary data, msgpack for binary, protobuf for typed.",
     "tags": ["python", "pickle", "rce", "review"]},
    {"id": "r12_dr_eval_user_input", "type": "anti_pattern", "maturity": "canon",
     "title": "eval() / exec() called on input parameters",
     "content": "+ result = eval(expr) — RCE. ast.literal_eval for literals; lark/simpleeval for math. exec is even worse; remove it.",
     "tags": ["python", "eval", "rce", "review"]},
    {"id": "r12_dr_open_no_close", "type": "anti_pattern", "maturity": "validated",
     "title": "open() without context manager leaks file descriptors",
     "content": "+ f = open(path); data = f.read() — fd leak on exception. Use with open(...) as f. CPython GC closes eventually but PyPy / forks linger.",
     "tags": ["python", "files", "resource-leak"]},
    {"id": "r12_dr_threading_lock_typo", "type": "anti_pattern", "maturity": "validated",
     "title": "threading.Lock() created inside the function it protects",
     "content": "+ def f(): lock = threading.Lock(); with lock: ... — every call gets its own lock = no mutual exclusion. Lock must be module/class-level.",
     "tags": ["python", "threading", "concurrency"]},
    {"id": "r12_dr_console_log_in_pr", "type": "anti_pattern", "maturity": "validated",
     "title": "console.log left in JS/TS production code",
     "content": "+ console.log('debug', user) — leaks PII to browser console, ships extra bytes. Add no-console ESLint rule; allow only console.warn/error.",
     "tags": ["javascript", "typescript", "logging", "review"]},
    {"id": "r12_dr_setstate_mutate", "type": "anti_pattern", "maturity": "validated",
     "title": "setState mutating prev state in callback",
     "content": "+ setState(prev => { prev.x = 1; return prev }) — same reference, no re-render. Return a new object: { ...prev, x: 1 }.",
     "tags": ["react", "state", "mutation", "review"]},
    {"id": "r12_dr_useeffect_no_deps", "type": "anti_pattern", "maturity": "canon",
     "title": "useEffect call without dependency array in diff",
     "content": "+ useEffect(() => { fetch(...) }) — runs every render = infinite loop. Provide [] for mount-only or list every reactive dep.",
     "tags": ["react", "useEffect", "review"]},
    {"id": "r12_dr_time_sleep_async", "type": "anti_pattern", "maturity": "validated",
     "title": "time.sleep inside an async function",
     "content": "+ async def f(): time.sleep(5) — blocks the event loop = stalls every coroutine. Use await asyncio.sleep(5).",
     "tags": ["python", "async", "blocking", "review"]},
    {"id": "r12_dr_dangerously_set_html", "type": "anti_pattern", "maturity": "canon",
     "title": "dangerouslySetInnerHTML with user-controlled content",
     "content": "+ <div dangerouslySetInnerHTML={{ __html: post.body }} /> — XSS. Use a sanitizer (DOMPurify) or render markdown via a safe library.",
     "tags": ["react", "xss", "security", "review"]},
    {"id": "r12_dr_fs_unlink_user_path", "type": "anti_pattern", "maturity": "canon",
     "title": "fs.unlink / os.remove on a user-supplied path",
     "content": "+ os.remove(request.path) — combine with path traversal = arbitrary file deletion. Resolve canonical, assert under base, then unlink.",
     "tags": ["filesystem", "path-traversal", "security"]},

    # ── multilingual_lite (CS / DE pairs, ~16 entries) ────────────────────
    # CS = Czech, DE = German. Each pairs with an existing EN memory; the
    # eval expects the multilingual query to retrieve EITHER the EN sibling
    # OR the localised entry (asymmetric grade vector in queries).
    {"id": "r12_ml_cs_n1_orm", "type": "anti_pattern", "maturity": "validated",
     "title": "N+1 dotazy v ORM smyčkách (cs)",
     "content": "for x in q.all(): x.related spustí 1+N dotazů. Použij joinedload nebo selectinload; jinak server zaboje pod zátěží.",
     "tags": ["database", "orm", "perf", "cs"]},
    {"id": "r12_ml_cs_http_timeout", "type": "pattern", "maturity": "canon",
     "title": "Vždy nastav timeout u HTTP volání (cs)",
     "content": "requests.get(url, timeout=10). Bez timeoutu zaseknutý upstream zablokuje workera napořád a vyčerpá pool.",
     "tags": ["http", "timeout", "reliability", "cs"]},
    {"id": "r12_ml_cs_pytest_fixtures", "type": "pattern", "maturity": "canon",
     "title": "Používej pytest fixtures místo globálního stavu (cs)",
     "content": "Fixtures jsou izolované a explicitní. Globální mocky se rozbíjejí při změně pořadí testů; fixtures izolují setup pro každý test.",
     "tags": ["testing", "pytest", "python", "cs"]},
    {"id": "r12_ml_cs_sql_injection", "type": "pattern", "maturity": "canon",
     "title": "Parametrizuj SQL — nikdy f-string s uživatelským vstupem (cs)",
     "content": "f\"SELECT * FROM x WHERE id={uid}\" je SQL injection. ORM ?, psycopg %s, SQLAlchemy text(:id). Bez výjimek.",
     "tags": ["security", "sql", "injection", "cs"]},
    {"id": "r12_ml_cs_jwt_aud", "type": "pattern", "maturity": "canon",
     "title": "U JWT ověřuj podpis, audience i expiraci (cs)",
     "content": "Mnoho knihoven ve výchozím stavu audience neověřuje. Token z jiné služby ze stejné auth domény projde. Vždy assert iss/aud/exp.",
     "tags": ["security", "jwt", "auth", "cs"]},
    {"id": "r12_ml_cs_react_keys", "type": "pattern", "maturity": "canon",
     "title": "Stabilní klíče v React seznamech (cs)",
     "content": "key={item.id}, nikdy key={index}. Index způsobuje ztrátu fokusu, ztrátu vstupu a trhání animací při přerovnání.",
     "tags": ["frontend", "react", "lists", "cs"]},
    {"id": "r12_ml_cs_docker_root", "type": "anti_pattern", "maturity": "validated",
     "title": "Spouštění kontejnerů jako root (cs)",
     "content": "Útěk z kontejneru znamená root na hostiteli. V Dockerfile vždy USER nonroot; readonly rootfs kde to aplikace dovolí.",
     "tags": ["ops", "docker", "security", "cs"]},
    {"id": "r12_ml_cs_postgres_pool", "type": "pattern", "maturity": "validated",
     "title": "Velikost connection poolu: 2*workers + rezerva (cs)",
     "content": "Výchozí pool 5 hladoví pod zátěží. Nastav 2*N_workerů + 4. Sleduj latenci při získávání spojení.",
     "tags": ["database", "pool", "perf", "cs"]},
    {"id": "r12_ml_de_n1_orm", "type": "anti_pattern", "maturity": "validated",
     "title": "N+1 Abfragen in ORM-Schleifen (de)",
     "content": "for x in q.all(): x.related löst 1+N Abfragen aus. joinedload oder selectinload nutzen; sonst kapituliert der Server unter Last.",
     "tags": ["database", "orm", "perf", "de"]},
    {"id": "r12_ml_de_http_timeout", "type": "pattern", "maturity": "canon",
     "title": "HTTP-Timeout immer setzen (de)",
     "content": "requests.get(url, timeout=10). Ohne Timeout blockiert ein hängender Upstream den Worker für immer und erschöpft den Pool.",
     "tags": ["http", "timeout", "reliability", "de"]},
    {"id": "r12_ml_de_pytest_fixtures", "type": "pattern", "maturity": "canon",
     "title": "pytest-Fixtures statt globalem Zustand (de)",
     "content": "Fixtures sind isoliert und explizit. Globale Mocks brechen, wenn sich die Testreihenfolge ändert; Fixtures isolieren das Setup pro Test.",
     "tags": ["testing", "pytest", "python", "de"]},
    {"id": "r12_ml_de_sql_injection", "type": "pattern", "maturity": "canon",
     "title": "SQL parametrisieren — niemals f-String mit Benutzereingabe (de)",
     "content": "f\"SELECT * FROM x WHERE id={uid}\" ist SQL-Injection. ORM ?, psycopg %s, SQLAlchemy text(:id). Keine Ausnahmen.",
     "tags": ["security", "sql", "injection", "de"]},
    {"id": "r12_ml_de_jwt_aud", "type": "pattern", "maturity": "canon",
     "title": "JWT: Signatur, Audience und Ablauf prüfen (de)",
     "content": "Viele Bibliotheken prüfen die Audience nicht standardmäßig. Ein Token aus einem anderen Dienst derselben Auth-Domäne kommt durch. Immer iss/aud/exp asserten.",
     "tags": ["security", "jwt", "auth", "de"]},
    {"id": "r12_ml_de_react_keys", "type": "pattern", "maturity": "canon",
     "title": "Stabile Schlüssel in React-Listen (de)",
     "content": "key={item.id}, niemals key={index}. Index-Keys verursachen Fokus-Verlust, verlorenen Input und ruckelnde Animationen beim Umsortieren.",
     "tags": ["frontend", "react", "lists", "de"]},
    {"id": "r12_ml_de_docker_root", "type": "anti_pattern", "maturity": "validated",
     "title": "Container als Root ausführen (de)",
     "content": "Ein Container-Escape landet als Root auf dem Host. Immer USER nonroot im Dockerfile; readonly rootfs, wo die App es erlaubt.",
     "tags": ["ops", "docker", "security", "de"]},
    {"id": "r12_ml_de_postgres_pool", "type": "pattern", "maturity": "validated",
     "title": "Connection-Pool-Größe: 2*workers + Puffer (de)",
     "content": "Standard-Pool von 5 hungert unter Last. Tune auf 2*N_workers + 4. Beobachte Checkout-Latenz, um zu wissen, wann erhöht werden muss.",
     "tags": ["database", "pool", "perf", "de"]},

    # ── lexical_gap_hard (no-overlap paraphrastic, ~12 entries) ───────────
    {"id": "r12_lg_canary_traffic", "type": "pattern", "maturity": "canon",
     "title": "Gradual rollout via percentage-based traffic split",
     "content": "Send a small fraction of users to a new build first; observe error rate and latency before broadening. Roll back by flipping the load balancer.",
     "tags": ["deploy", "release", "rollout", "canary"]},
    {"id": "r12_lg_password_storage", "type": "pattern", "maturity": "canon",
     "title": "Memory-hard, salted, slow hashing for credential storage",
     "content": "Use Argon2id with tuned cost parameters. SHA-* and MD5 are designed to be fast and are GPU-vulnerable; never use them for password hashing.",
     "tags": ["security", "credentials", "hashing"]},
    {"id": "r12_lg_idempotent_post", "type": "pattern", "maturity": "canon",
     "title": "Replay-safe write endpoints via client-supplied request keys",
     "content": "Require a header that uniquely identifies the request. Store key+response for 24h; a duplicate POST returns the original outcome instead of writing again.",
     "tags": ["api", "idempotency", "retries"]},
    {"id": "r12_lg_observability_corr", "type": "pattern", "maturity": "canon",
     "title": "Correlate logs across services via a single propagated identifier",
     "content": "Inject a unique id at the edge, echo it on every downstream call, surface it in error messages. Customer reports an issue: grep one id, see the whole timeline.",
     "tags": ["ops", "observability", "logs"]},
    {"id": "r12_lg_dont_hang_forever", "type": "pattern", "maturity": "canon",
     "title": "Bound the wall time of any blocking call",
     "content": "Network calls, queue waits, file locks — every blocking operation needs a deadline. The default of 'forever' is the leading cause of hung workers.",
     "tags": ["reliability", "deadline", "timeout"]},
    {"id": "r12_lg_no_thundering_herd", "type": "pattern", "maturity": "validated",
     "title": "Stagger retry attempts to prevent synchronized stampedes",
     "content": "When a downstream comes back, every client retries at once and crushes it again. Add jitter to backoff; randomize the first retry instant.",
     "tags": ["reliability", "retry", "backoff"]},
    {"id": "r12_lg_blameless_review", "type": "pattern", "maturity": "canon",
     "title": "Treat post-incident reviews as system analysis, not personal critique",
     "content": "Naming people for outcomes chills future incident reports. Focus on the conditions that allowed the failure; outputs are concrete action items with owners.",
     "tags": ["culture", "incident", "process"]},
    {"id": "r12_lg_least_authority", "type": "pattern", "maturity": "canon",
     "title": "Grant the minimum capabilities a workload needs to function",
     "content": "Default to deny; expand only on demonstrated need. Applies to IAM, container caps, file modes, network egress. Reduces blast radius of every compromise.",
     "tags": ["security", "permissions", "iam"]},
    {"id": "r12_lg_immutable_artifacts", "type": "pattern", "maturity": "canon",
     "title": "Build once, promote the same artifact through every environment",
     "content": "Re-building per environment introduces drift. Tag the image once at build, promote it from staging to prod unchanged; environment differences live in config.",
     "tags": ["ops", "deploy", "supply-chain"]},
    {"id": "r12_lg_paid_for_speed", "type": "pattern", "maturity": "validated",
     "title": "When the change is small, ship it; when it's big, slice it",
     "content": "PRs above ~400 changed lines lose review attention. Decompose into reviewable slices; merge frequently. Small PRs ship faster and find bugs earlier.",
     "tags": ["process", "review", "engineering"]},
    {"id": "r12_lg_user_journey_slo", "type": "pattern", "maturity": "validated",
     "title": "Define reliability targets per critical user journey",
     "content": "An overall uptime number tells you nothing about whether checkout works. Pick a handful of journeys, set per-journey latency and success goals, alert on those.",
     "tags": ["ops", "reliability", "slo"]},
    {"id": "r12_lg_warm_state_on_boot", "type": "pattern", "maturity": "tested",
     "title": "Pay first-request costs before traffic arrives",
     "content": "ORM metadata, model weights, connection pools — everything that loads on demand will be slow for the first user. Hit a warmup endpoint at deploy before flipping the LB.",
     "tags": ["perf", "startup", "deploy"]},

    # ── extra code_specific to round out cluster size (~8 more) ───────────
    {"id": "r12_cs_pytest_fixture_scope", "type": "pattern", "maturity": "validated",
     "title": "pytest fixture scope='session' for expensive setup",
     "content": "@pytest.fixture(scope='session') runs once per test session. Use for DB engines, HTTP servers, model loads. Pair with autouse=False so it's opt-in.",
     "tags": ["pytest", "fixtures", "testing"]},
    {"id": "r12_cs_django_select_related", "type": "pattern", "maturity": "validated",
     "title": "Django select_related for foreign keys, prefetch_related for reverse",
     "content": "select_related issues a JOIN (FK to one); prefetch_related runs a follow-up query (reverse FK / m2m). Mixing them on the same QuerySet handles deep graphs.",
     "tags": ["django", "orm", "perf"]},
    {"id": "r12_cs_npm_audit_signatures", "type": "pattern", "maturity": "validated",
     "title": "npm audit signatures + npm ci in CI",
     "content": "npm ci enforces the lockfile (no silent floats). npm audit signatures verifies maintainer signatures (registry-side provenance). Both gate the build.",
     "tags": ["npm", "supply-chain", "ci", "security"]},
    {"id": "r12_cs_swr_revalidate", "type": "pattern", "maturity": "validated",
     "title": "SWR revalidateOnFocus for fresh-on-tab-return UX",
     "content": "useSWR refetches when the tab regains focus; users coming back from email see fresh data. Disable on slow APIs to avoid retry storms.",
     "tags": ["swr", "react", "data-fetching"]},
    {"id": "r12_cs_pyproject_uv_pin", "type": "pattern", "maturity": "validated",
     "title": "uv pip compile pins transitive deps for reproducible installs",
     "content": "pyproject.toml is loose; uv pip compile (or pip-tools) emits a fully resolved requirements.lock. Commit the lockfile; CI runs uv pip sync.",
     "tags": ["uv", "python", "dependencies"]},
    {"id": "r12_cs_go_context_cancel", "type": "pattern", "maturity": "validated",
     "title": "Go: context.WithCancel + defer cancel() in every fan-out",
     "content": "Without defer cancel() child goroutines leak when the caller errors. ctx, cancel := context.WithCancel(parent); defer cancel(). Same for WithTimeout.",
     "tags": ["go", "context", "concurrency"]},
    {"id": "r12_cs_typescript_strict", "type": "pattern", "maturity": "validated",
     "title": "TypeScript strict + noUncheckedIndexedAccess in tsconfig",
     "content": "strict turns on every checker; noUncheckedIndexedAccess catches the silent any from arr[i]. Apply on new packages; ratchet legacy ones.",
     "tags": ["typescript", "tsconfig", "typing"]},
    {"id": "r12_cs_pgvector_filter_pre", "type": "pattern", "maturity": "tested",
     "title": "pgvector pre-filter via WHERE org_id = ... before ORDER BY <->",
     "content": "Postgres planner can't push a vector ORDER BY through a filter; partial index per tenant or pre-filter in a CTE keeps recall while honouring multi-tenancy.",
     "tags": ["pgvector", "postgres", "multi-tenant"]},
]


# ── Labelled queries: 50+ with graded relevance {1, 2, 3} ───────────────────
# Convention:
#   3 = the canon answer for this query (usually exactly one memory)
#   2 = strong supporter / direct alternative
#   1 = topical neighbour (same domain, partial fit)
# Most queries label 3-7 memories; everything unlabelled implicitly = 0.

QUERIES: list[dict] = [
    # ── Block A: 20 realistic agent task descriptions ──────────────────────
    {"q": "optimize N+1 in Django ORM", "cluster": "code_specific",
     "rel": [("db01", 3), ("db05", 2), ("api08", 1), ("perf01", 1)]},
    {"q": "set up Pydantic validation for FastAPI request body", "cluster": "code_specific",
     "rel": [("api03", 3), ("arch01", 2), ("api10", 1), ("api15", 1)]},
    {"q": "harden Redis against unauthorized access", "cluster": "anti_pattern_intent",
     "rel": [("sec03", 3), ("sec07", 2), ("sec13", 2), ("api13", 1)]},
    {"q": "write integration tests against a real database", "cluster": "paraphrastic",
     "rel": [("test02", 3), ("test01", 2), ("test08", 2), ("db04", 1)]},
    {"q": "fix flaky tests that sleep waiting for async work", "cluster": "anti_pattern_intent",
     "rel": [("test06", 3), ("test05", 2), ("test13", 2), ("perf04", 1)]},
    {"q": "speed up FastAPI cold start time", "cluster": "code_specific",
     "rel": [("perf05", 3), ("perf08", 2), ("ml02", 2), ("perf01", 1)]},
    {"q": "implement idempotent POST endpoint", "cluster": "paraphrastic",
     "rel": [("api04", 3), ("db13", 2), ("api01", 1)]},
    {"q": "tune Postgres connection pool for FastAPI under load", "cluster": "code_specific",
     "rel": [("perf02", 3), ("db10", 3), ("ops01", 1), ("perf01", 1)]},
    {"q": "add structured request-scoped logging", "cluster": "paraphrastic",
     "rel": [("ops02", 3), ("api14", 2), ("ops09", 2), ("sec15", 1)]},
    {"q": "validate JWT correctly so cross-audience tokens are rejected", "cluster": "code_specific",
     "rel": [("sec05", 3), ("sec06", 2), ("sec02", 1), ("api13", 1)]},
    {"q": "build a hybrid BM25 plus vector retrieval pipeline", "cluster": "code_specific",
     "rel": [("ml03", 3), ("arch10", 3), ("db12", 2), ("ml07", 2), ("obs03", 1)]},
    {"q": "deploy a new service safely without downtime", "cluster": "paraphrastic",
     "rel": [("ops04", 3), ("perf08", 2), ("ops05", 1), ("api16", 1)]},
    {"q": "shrink the Docker image for a Python service", "cluster": "paraphrastic",
     "rel": [("ops06", 3), ("ops07", 2), ("perf05", 1)]},
    {"q": "GDPR right-to-be-forgotten implementation", "cluster": "paraphrastic",
     "rel": [("sec11", 3), ("sec08", 2), ("less04", 1)]},
    {"q": "prevent SQL injection in a dynamic query builder", "cluster": "anti_pattern_intent",
     "rel": [("sec09", 3), ("sec01", 2), ("sec12", 1)]},
    {"q": "paginate an endpoint that returns thousands of rows", "cluster": "paraphrastic",
     "rel": [("api12", 3), ("api11", 3), ("perf07", 2), ("db03", 1)]},
    {"q": "reduce React bundle size on a dashboard route", "cluster": "code_specific",
     "rel": [("fe05", 3), ("fe12", 2), ("fe02", 2), ("fe09", 1), ("fe03", 1)]},
    {"q": "instrument distributed tracing across microservices", "cluster": "paraphrastic",
     "rel": [("ops09", 3), ("arch07", 2), ("ops02", 2), ("api14", 1)]},
    {"q": "diagnose a hanging outbound HTTP call in a worker", "cluster": "paraphrastic",
     "rel": [("api01", 3), ("perf09", 2), ("perf02", 1)]},
    {"q": "write reproducible ML training scripts", "cluster": "paraphrastic",
     "rel": [("ml01", 3), ("ml04", 2), ("ml06", 2), ("ml05", 1)]},

    # ── Block B: 30 type × intent verb combinations ────────────────────────
    # Test ✕ pattern
    {"q": "best practice for writing fixtures in pytest", "cluster": "code_specific",
     "rel": [("test01", 3), ("test07", 2), ("test10", 1), ("test14", 1)]},
    {"q": "improve test coverage with branch coverage", "cluster": "code_specific",
     "rel": [("test09", 3), ("test12", 2), ("test11", 1)]},
    {"q": "snapshot testing for large outputs", "cluster": "code_specific",
     "rel": [("test03", 3), ("test07", 1), ("test01", 1)]},
    # Test ✕ anti_pattern
    {"q": "tests fail in CI but pass locally — why", "cluster": "paraphrastic",
     "rel": [("test13", 3), ("test06", 2), ("test05", 2), ("less05", 1)]},
    # Fix ✕ lesson / anti_pattern
    {"q": "fix money rounding bug in invoice totals", "cluster": "anti_pattern_intent",
     "rel": [("less06", 3), ("ml04", 1)]},
    {"q": "fix mutable default argument bug in Python", "cluster": "anti_pattern_intent",
     "rel": [("less01", 3), ("test13", 1)]},
    {"q": "fix soft-delete rows leaking into queries", "cluster": "anti_pattern_intent",
     "rel": [("less04", 3), ("db09", 1), ("db11", 1)]},
    # Optimize ✕ pattern
    {"q": "optimize SQL query that orders by created_at", "cluster": "code_specific",
     "rel": [("db03", 3), ("db08", 2), ("db06", 1)]},
    {"q": "optimize fan-out network calls in a worker", "cluster": "paraphrastic",
     "rel": [("perf04", 3), ("perf11", 2), ("perf10", 1), ("api01", 1)]},
    {"q": "optimize JSON serialization of large responses", "cluster": "code_specific",
     "rel": [("perf12", 3), ("perf07", 2), ("perf13", 1)]},
    # Secure ✕ anti_pattern
    {"q": "secure app against eval-style remote code execution", "cluster": "anti_pattern_intent",
     "rel": [("sec01", 3), ("sec09", 1)]},
    {"q": "stop logging sensitive auth headers in access logs", "cluster": "anti_pattern_intent",
     "rel": [("sec15", 3), ("api17", 2), ("ops02", 1)]},
    {"q": "what to avoid when running containers in production", "cluster": "anti_pattern_intent",
     "rel": [("ops07", 3), ("sec10", 2), ("sec03", 1)]},
    # Decide ✕ decision
    {"q": "should we adopt FastAPI for new services", "cluster": "paraphrastic",
     "rel": [("api15", 3), ("arch01", 3), ("arch09", 2)]},
    {"q": "decision: monorepo or polyrepo for our services", "cluster": "paraphrastic",
     "rel": [("arch02", 3), ("arch05", 1)]},
    {"q": "should we use SQLite or Postgres for a new tool", "cluster": "paraphrastic",
     "rel": [("db04", 3), ("arch04", 2), ("arch08", 2), ("db17", 2)]},
    {"q": "RRF or linear blend for hybrid search ranking", "cluster": "code_specific",
     "rel": [("arch10", 3), ("ml03", 2)]},
    # Test ✕ pattern (e2e specifically)
    {"q": "how many end-to-end tests are too many", "cluster": "paraphrastic",
     "rel": [("test11", 3), ("test14", 2), ("test10", 1)]},
    # Db ✕ pattern (transactions)
    {"q": "transactional consistency for multi-step writes", "cluster": "paraphrastic",
     "rel": [("db07", 3), ("db13", 2), ("db11", 1)]},
    # Db ✕ anti_pattern (sharding)
    {"q": "should I shard the database now", "cluster": "paraphrastic",
     "rel": [("db16", 3), ("db15", 2), ("arch08", 2), ("db17", 1)]},
    # Db ✕ migrations
    {"q": "safely run a migration on a huge production table", "cluster": "paraphrastic",
     "rel": [("less02", 3), ("db09", 2), ("ops04", 1)]},
    # API ✕ rate limiting
    {"q": "rate limit login endpoint against credential stuffing", "cluster": "anti_pattern_intent",
     "rel": [("sec13", 3), ("api06", 2), ("sec07", 1)]},
    # API ✕ pattern (versioning)
    {"q": "version a public REST API", "cluster": "paraphrastic",
     "rel": [("api05", 3), ("api10", 2), ("api11", 1)]},
    # API ✕ GraphQL
    {"q": "GraphQL resolver causing N+1", "cluster": "code_specific",
     "rel": [("api08", 3), ("db01", 2), ("db05", 1)]},
    # Frontend ✕ pattern
    {"q": "manage server state in a React app", "cluster": "paraphrastic",
     "rel": [("fe04", 3), ("fe08", 2), ("fe02", 1)]},
    {"q": "fix hydration mismatch in Next.js", "cluster": "anti_pattern_intent",
     "rel": [("fe11", 3), ("fe03", 2)]},
    {"q": "make the site keyboard-accessible", "cluster": "paraphrastic",
     "rel": [("fe10", 3), ("fe06", 1)]},
    # Ops ✕ pattern
    {"q": "set Kubernetes resource requests and limits", "cluster": "code_specific",
     "rel": [("ops08", 3), ("perf02", 1), ("ops05", 1)]},
    {"q": "monitor cron jobs so they don't silently break", "cluster": "paraphrastic",
     "rel": [("less03", 3), ("ops02", 1), ("ops11", 1)]},
    # Ml ✕ lesson
    {"q": "evaluate retrieval quality before tuning prompts", "cluster": "paraphrastic",
     "rel": [("ml07", 3), ("ml04", 2), ("ml03", 2), ("obs03", 1)]},
    # Ml ✕ pattern (cache)
    {"q": "cache deterministic LLM calls to cut cost", "cluster": "paraphrastic",
     "rel": [("ml08", 3), ("perf03", 1)]},
    # Test ✕ benchmark regressions
    {"q": "catch performance regressions in CI", "cluster": "paraphrastic",
     "rel": [("test12", 3), ("fe12", 2), ("perf09", 1)]},
    # Lesson ✕ ops
    {"q": "feature flag service outage took down our app", "cluster": "paraphrastic",
     "rel": [("less08", 3), ("ops05", 1), ("less03", 1)]},
    # Architecture ✕ streaming
    {"q": "stream events to a browser one-way", "cluster": "paraphrastic",
     "rel": [("arch11", 3), ("perf07", 2)]},
    # Pattern ✕ ETag/cache
    {"q": "reduce bandwidth on polling clients", "cluster": "paraphrastic",
     "rel": [("perf13", 3), ("perf03", 2), ("api12", 1)]},

    # ══════════════════════════════════════════════════════════════════════
    # R12 expansion (151 new queries → 206 total)
    # Each query carries a `cluster` so eval slices per-cluster.
    # ══════════════════════════════════════════════════════════════════════

    # ── Block C: code_specific (26 new, identifiers/keywords searchable) ──
    {"q": "configure pgvector hnsw index", "cluster": "code_specific",
     "rel": [("r12_cs_pgvector_hnsw", 3), ("db12", 2), ("r12_cs_pgvector_filter_pre", 1)]},
    {"q": "sqlite-vec serialize_float32 example", "cluster": "code_specific",
     "rel": [("r12_cs_sqlite_vec_serialize", 3), ("db12", 2)]},
    {"q": "argon2id parameters 2025", "cluster": "code_specific",
     "rel": [("r12_cs_argon2_params_2025", 3), ("sec02", 2), ("r12_on_argon2_first", 2)]},
    {"q": "uvicorn workers vs gunicorn", "cluster": "code_specific",
     "rel": [("r12_cs_uvicorn_workers", 3), ("perf02", 1)]},
    {"q": "alembic autogenerate sqlite", "cluster": "code_specific",
     "rel": [("r12_cs_alembic_autogen", 3), ("db09", 1)]},
    {"q": "pytest parametrize ids", "cluster": "code_specific",
     "rel": [("r12_cs_pytest_parametrize_ids", 3), ("test01", 1), ("test07", 1)]},
    {"q": "pytest-asyncio strict mode", "cluster": "code_specific",
     "rel": [("r12_cs_pytest_asyncio_strict", 3), ("test01", 1), ("perf04", 1)]},
    {"q": "orjson numpy serialization", "cluster": "code_specific",
     "rel": [("r12_cs_orjson_default", 3), ("perf12", 2)]},
    {"q": "httpx AsyncClient FastAPI lifespan", "cluster": "code_specific",
     "rel": [("r12_cs_httpx_async_client", 3), ("api01", 2), ("r12_on_fastapi_basics", 1)]},
    {"q": "celery acks_late prefetch", "cluster": "code_specific",
     "rel": [("r12_cs_celery_acks_late", 3), ("api04", 1)]},
    {"q": "redis SET NX EX distributed lock", "cluster": "code_specific",
     "rel": [("r12_cs_redis_setnx_lock", 3), ("r12_ap_no_ttl_redis", 1)]},
    {"q": "psycopg3 pipeline mode", "cluster": "code_specific",
     "rel": [("r12_cs_psycopg3_pipeline", 3), ("perf11", 1)]},
    {"q": "ruff select rules baseline", "cluster": "code_specific",
     "rel": [("r12_cs_ruff_select", 3)]},
    {"q": "mypy strict per package", "cluster": "code_specific",
     "rel": [("r12_cs_mypy_strict", 3)]},
    {"q": "react-hooks exhaustive-deps as error", "cluster": "code_specific",
     "rel": [("r12_cs_react_hook_deps", 3), ("fe01", 3), ("r12_dr_useeffect_no_deps", 2)]},
    {"q": "TanStack Query key factory", "cluster": "code_specific",
     "rel": [("r12_cs_tanstack_query_keys", 3), ("fe04", 2)]},
    {"q": "opentelemetry-instrument python auto", "cluster": "code_specific",
     "rel": [("r12_cs_otel_python_auto", 3), ("ops09", 2), ("arch07", 1)]},
    {"q": "pgbouncer transaction pool prepared statements", "cluster": "code_specific",
     "rel": [("r12_cs_pgbouncer_transaction", 3), ("db10", 1), ("perf02", 1)]},
    {"q": "systemd Restart on-failure StartLimitBurst", "cluster": "code_specific",
     "rel": [("r12_cs_systemd_unit_restart", 3)]},
    {"q": "terraform lifecycle prevent_destroy", "cluster": "code_specific",
     "rel": [("r12_cs_terraform_lifecycle", 3), ("r12_on_terraform_first", 2)]},
    {"q": "pytest fixture scope session", "cluster": "code_specific",
     "rel": [("r12_cs_pytest_fixture_scope", 3), ("test01", 2), ("r12_cs_pytest_parametrize_ids", 1)]},
    {"q": "django select_related vs prefetch_related", "cluster": "code_specific",
     "rel": [("r12_cs_django_select_related", 3), ("db01", 2), ("db05", 2)]},
    {"q": "npm ci npm audit signatures", "cluster": "code_specific",
     "rel": [("r12_cs_npm_audit_signatures", 3), ("r12_ap_dependency_pin_caret", 2)]},
    {"q": "swr revalidateOnFocus", "cluster": "code_specific",
     "rel": [("r12_cs_swr_revalidate", 3), ("fe04", 1)]},
    {"q": "uv pip compile lockfile", "cluster": "code_specific",
     "rel": [("r12_cs_pyproject_uv_pin", 3)]},
    {"q": "go context WithCancel defer", "cluster": "code_specific",
     "rel": [("r12_cs_go_context_cancel", 3)]},
    {"q": "typescript noUncheckedIndexedAccess", "cluster": "code_specific",
     "rel": [("r12_cs_typescript_strict", 3)]},

    # ── Block D: paraphrastic (14 new, intent without identifier) ─────────
    {"q": "stop one slow customer from blocking everyone else", "cluster": "paraphrastic",
     "rel": [("api06", 3), ("sec13", 2), ("api11", 1)]},
    {"q": "make sure a retry doesn't charge the customer twice", "cluster": "paraphrastic",
     "rel": [("api04", 3), ("r12_lg_idempotent_post", 3), ("db13", 2)]},
    {"q": "I want to be paged when checkout slows down for users", "cluster": "paraphrastic",
     "rel": [("ops15", 3), ("ops05", 2), ("perf09", 2), ("r12_lg_user_journey_slo", 2)]},
    {"q": "the database is fine but my app times out anyway", "cluster": "paraphrastic",
     "rel": [("perf02", 3), ("db10", 2), ("ops01", 2)]},
    {"q": "we need to roll something risky out without taking the site down", "cluster": "paraphrastic",
     "rel": [("ops04", 3), ("r12_lg_canary_traffic", 3), ("perf08", 1)]},
    {"q": "rotation of secrets is currently a forgotten checklist item", "cluster": "paraphrastic",
     "rel": [("sec07", 3), ("sec03", 1)]},
    {"q": "every release we re-build the image and weird stuff happens", "cluster": "paraphrastic",
     "rel": [("r12_lg_immutable_artifacts", 3), ("ops14", 2), ("ops06", 1)]},
    {"q": "I want our PR reviews to flag obvious security mistakes", "cluster": "paraphrastic",
     "rel": [("sec12", 3), ("sec14", 2), ("ops03", 1)]},
    {"q": "queries are right but the page is slow for the people far away", "cluster": "paraphrastic",
     "rel": [("r12_cs_psycopg3_pipeline", 2), ("perf04", 2), ("perf11", 2)]},
    {"q": "newcomers don't know our standard Python web stack", "cluster": "paraphrastic",
     "rel": [("arch01", 3), ("api15", 2), ("r12_on_fastapi_basics", 2)]},
    {"q": "incident reports turn into finger-pointing", "cluster": "paraphrastic",
     "rel": [("ops11", 3), ("r12_lg_blameless_review", 3)]},
    {"q": "scoping creep — we keep granting more access than needed", "cluster": "paraphrastic",
     "rel": [("r12_lg_least_authority", 3), ("sec07", 1)]},
    {"q": "PRs are too big to review properly", "cluster": "paraphrastic",
     "rel": [("r12_lg_paid_for_speed", 3), ("ops03", 1)]},
    {"q": "first user after deploy always sees a long wait", "cluster": "paraphrastic",
     "rel": [("r12_lg_warm_state_on_boot", 3), ("perf08", 3), ("ml02", 2)]},

    # ── Block E: anti_pattern_intent (21 new, severity-leaning) ───────────
    {"q": "fix RSA private key sitting in environment variable", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_rsa_key_in_env", 3), ("sec03", 2)]},
    {"q": "harden webhook endpoint against forged calls", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_unverified_webhooks", 3), ("sec05", 1)]},
    {"q": "avoid pickle.loads on data from clients", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_pickle_untrusted", 3), ("sec01", 2), ("r12_dr_pickle_in_diff", 2)]},
    {"q": "prevent yaml.load from running arbitrary Python", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_yaml_load_unsafe", 3), ("sec01", 1)]},
    {"q": "prevent XXE in our XML parsing path", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_xxe_xml", 3), ("sec01", 1)]},
    {"q": "fix open redirect via next parameter", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_open_redirect", 3)]},
    {"q": "harden against SSRF when fetching user URLs", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_ssrf_url_fetch", 3), ("r12_ap_unverified_webhooks", 1)]},
    {"q": "avoid timing attacks comparing tokens", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_timing_unsafe_compare", 3), ("sec02", 1)]},
    {"q": "fix path traversal where users supply filenames", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_path_traversal", 3), ("r12_dr_fs_unlink_user_path", 2)]},
    {"q": "harden CORS so origin star with credentials cannot leak", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_cors_star_creds", 3), ("sec18", 1)]},
    {"q": "what to do if a JWT signing secret was committed to git", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_jwt_secret_in_repo", 3), ("sec07", 2), ("sec06", 1)]},
    {"q": "never disable CSRF middleware globally", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_disable_csrf_for_api", 3), ("sec17", 2)]},
    {"q": "avoid mass-assignment of body to ORM model", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_mass_assign", 3), ("api03", 2)]},
    {"q": "stop logging full user objects with PII", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_log_pii", 3), ("sec15", 2), ("sec11", 1)]},
    {"q": "avoid caret ranges in production lockfile", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_dependency_pin_caret", 3), ("ops14", 2)]},
    {"q": "prevent stack overflow from unbounded recursion in parser", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_unbounded_recursion", 3)]},
    {"q": "fix catastrophic backtracking regex causing thread hang", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_regex_redos", 3), ("r12_ap_unbounded_recursion", 1)]},
    {"q": "never call time.sleep inside a request handler", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_sleep_in_request", 3), ("r12_dr_time_sleep_async", 2), ("perf04", 1)]},
    {"q": "avoid except Exception pass swallowing errors", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_swallow_exceptions", 3), ("api02", 2)]},
    {"q": "fix module-level DB session shared across threads", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_global_db_session", 3), ("db10", 1)]},
    {"q": "prevent unbounded Redis growth — always set TTL", "cluster": "anti_pattern_intent",
     "rel": [("r12_ap_no_ttl_redis", 3), ("perf06", 2)]},

    # ── Block F: onboarding_to_stack (25 new, multi-canon) ────────────────
    {"q": "I'm new to Postgres, what should I learn first", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_postgres_first", 3), ("db17", 2), ("r12_on_postgres_locks", 2),
             ("db03", 1), ("db08", 1)]},
    {"q": "joining the team — give me Postgres essentials", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_postgres_first", 3), ("r12_on_postgres_locks", 2), ("db18", 1)]},
    {"q": "Postgres lock pitfalls for new operators", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_postgres_locks", 3), ("less02", 2), ("db11", 1)]},
    {"q": "I'm new to FastAPI, what should I know first", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_fastapi_basics", 3), ("api15", 2), ("arch01", 2), ("api03", 1)]},
    {"q": "FastAPI testing for newcomers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_fastapi_testing", 3), ("test01", 2), ("test02", 1)]},
    {"q": "I'm new to React, what to learn first", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_react_first", 3), ("fe14", 2), ("fe04", 2), ("fe01", 1)]},
    {"q": "Next.js App Router for newcomers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_nextjs_first", 3), ("fe03", 2), ("fe11", 1)]},
    {"q": "Tailwind CSS essentials for new developers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_tailwind_first", 3), ("fe02", 2)]},
    {"q": "I'm new to Argon2 password hashing", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_argon2_first", 3), ("sec02", 2), ("r12_cs_argon2_params_2025", 2)]},
    {"q": "I'm new to SQLAlchemy 2, what should I know", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_sqlalchemy_first", 3), ("db05", 2), ("db01", 2), ("arch01", 1)]},
    {"q": "Pydantic v2 essentials for newcomers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_pydantic_first", 3), ("api03", 2), ("less01", 1)]},
    {"q": "I'm new to Docker, give me the essentials", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_docker_first", 3), ("ops06", 2), ("ops07", 2), ("ops14", 1)]},
    {"q": "Kubernetes essentials for new operators", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_kubernetes_first", 3), ("ops08", 2), ("ops01", 2)]},
    {"q": "I'm new to OpenTelemetry, what to set up first", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_otel_first", 3), ("arch07", 2), ("ops09", 2), ("r12_cs_otel_python_auto", 2)]},
    {"q": "I'm new to Terraform, where do I start", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_terraform_first", 3), ("r12_cs_terraform_lifecycle", 2)]},
    {"q": "onboarding for our Python web stack", "cluster": "onboarding_to_stack",
     "rel": [("arch01", 3), ("r12_on_fastapi_basics", 2), ("r12_on_pydantic_first", 2),
             ("r12_on_sqlalchemy_first", 2)]},
    {"q": "what should a new backend engineer learn this week", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_postgres_first", 2), ("r12_on_fastapi_basics", 2), ("r12_on_docker_first", 2)]},
    {"q": "what should a new frontend engineer learn this week", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_react_first", 3), ("r12_on_nextjs_first", 2), ("r12_on_tailwind_first", 2)]},
    {"q": "explain Postgres EXPLAIN ANALYZE for a new dev", "cluster": "onboarding_to_stack",
     "rel": [("db08", 3), ("r12_on_postgres_first", 2), ("db03", 1)]},
    {"q": "FastAPI dependency injection for newcomers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_fastapi_basics", 3), ("api03", 2), ("arch01", 1)]},
    {"q": "React hooks rules for newcomers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_react_first", 3), ("fe01", 2), ("r12_cs_react_hook_deps", 2), ("fe07", 1)]},
    {"q": "kubernetes probes — readiness vs liveness for newcomers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_kubernetes_first", 3), ("ops01", 2)]},
    {"q": "Tailwind tokens — how do I customize the theme", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_tailwind_first", 3), ("fe02", 1)]},
    {"q": "what does a Pydantic v2 validator look like", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_pydantic_first", 3), ("api03", 1)]},
    {"q": "Docker multi-stage build for new developers", "cluster": "onboarding_to_stack",
     "rel": [("r12_on_docker_first", 3), ("ops06", 3), ("ops14", 1)]},

    # ── Block G: diff_review (30 new, pasted snippet → AP) ────────────────
    # Each query pastes a small additive diff hunk. Gold answer is the AP
    # that the review path should flag.
    {"q": "+ requests.get(url)\n+ resp.json()", "cluster": "diff_review",
     "rel": [("r12_dr_requests_no_timeout", 3), ("api01", 3)]},
    {"q": "+ requests.post(api, json=body)", "cluster": "diff_review",
     "rel": [("r12_dr_requests_no_timeout", 3), ("api01", 2)]},
    {"q": "+ print('user', user.email)", "cluster": "diff_review",
     "rel": [("r12_dr_print_in_handler", 3), ("r12_ap_log_pii", 2)]},
    {"q": "+ assert request.user.is_authenticated", "cluster": "diff_review",
     "rel": [("r12_dr_assert_for_validation", 3)]},
    {"q": "+ subprocess.run(f'rm -rf {path}', shell=True)", "cluster": "diff_review",
     "rel": [("r12_dr_subprocess_shell_true", 3), ("sec01", 1)]},
    {"q": "+ q = f\"SELECT * FROM users WHERE id = {uid}\"", "cluster": "diff_review",
     "rel": [("r12_dr_str_format_sql", 3), ("sec09", 3)]},
    {"q": "+ hashlib.md5(password.encode()).hexdigest()", "cluster": "diff_review",
     "rel": [("r12_dr_md5_password", 3), ("sec02", 2)]},
    {"q": "+ data = pickle.loads(request.body)", "cluster": "diff_review",
     "rel": [("r12_dr_pickle_in_diff", 3), ("r12_ap_pickle_untrusted", 3)]},
    {"q": "+ result = eval(request.GET['expr'])", "cluster": "diff_review",
     "rel": [("r12_dr_eval_user_input", 3), ("sec01", 3)]},
    {"q": "+ f = open(path)\n+ data = f.read()", "cluster": "diff_review",
     "rel": [("r12_dr_open_no_close", 3)]},
    {"q": "+ def f():\n+     lock = threading.Lock()\n+     with lock:\n+         ...", "cluster": "diff_review",
     "rel": [("r12_dr_threading_lock_typo", 3)]},
    {"q": "+ console.log('debug user', user)", "cluster": "diff_review",
     "rel": [("r12_dr_console_log_in_pr", 3), ("r12_ap_log_pii", 1)]},
    {"q": "+ setState(prev => { prev.count = 1; return prev })", "cluster": "diff_review",
     "rel": [("r12_dr_setstate_mutate", 3), ("fe07", 3)]},
    {"q": "+ useEffect(() => { fetchData() })", "cluster": "diff_review",
     "rel": [("r12_dr_useeffect_no_deps", 3), ("fe01", 3), ("r12_cs_react_hook_deps", 2)]},
    {"q": "+ async def f():\n+     time.sleep(5)", "cluster": "diff_review",
     "rel": [("r12_dr_time_sleep_async", 3), ("r12_ap_sleep_in_request", 2), ("perf04", 1)]},
    {"q": "+ <div dangerouslySetInnerHTML={{ __html: post.body }} />", "cluster": "diff_review",
     "rel": [("r12_dr_dangerously_set_html", 3)]},
    {"q": "+ os.remove(request.GET['path'])", "cluster": "diff_review",
     "rel": [("r12_dr_fs_unlink_user_path", 3), ("r12_ap_path_traversal", 3)]},
    {"q": "+ try:\n+     do_thing()\n+ except Exception:\n+     pass", "cluster": "diff_review",
     "rel": [("r12_ap_swallow_exceptions", 3), ("api02", 2)]},
    {"q": "+ yaml.load(open(path))", "cluster": "diff_review",
     "rel": [("r12_ap_yaml_load_unsafe", 3)]},
    {"q": "+ if token == request.headers['X-Token']:", "cluster": "diff_review",
     "rel": [("r12_ap_timing_unsafe_compare", 3)]},
    {"q": "+ return redirect(request.GET['next'])", "cluster": "diff_review",
     "rel": [("r12_ap_open_redirect", 3)]},
    {"q": "+ requests.get(request.GET['url']).text", "cluster": "diff_review",
     "rel": [("r12_ap_ssrf_url_fetch", 3), ("r12_dr_requests_no_timeout", 2)]},
    {"q": "+ user = User(**request.json)", "cluster": "diff_review",
     "rel": [("r12_ap_mass_assign", 3), ("api03", 1)]},
    {"q": "+ \"requests\": \"^2.31.0\"", "cluster": "diff_review",
     "rel": [("r12_ap_dependency_pin_caret", 3), ("ops14", 1)]},
    {"q": "+ session = Session()  # module-level", "cluster": "diff_review",
     "rel": [("r12_ap_global_db_session", 3)]},
    {"q": "+ redis.set(key, value)  # no EX", "cluster": "diff_review",
     "rel": [("r12_ap_no_ttl_redis", 3), ("r12_cs_redis_setnx_lock", 2)]},
    {"q": "+ kubectl apply -f manifests/", "cluster": "diff_review",
     "rel": [("r12_ap_kubectl_apply_in_ci", 3), ("ops10", 1), ("ops04", 1)]},
    {"q": "+ logger.info(f'user logged in: {user}')", "cluster": "diff_review",
     "rel": [("r12_ap_log_pii", 3), ("sec15", 2)]},
    {"q": "+ Pattern.compile(\"(a+)+$\").matcher(input).matches()", "cluster": "diff_review",
     "rel": [("r12_ap_regex_redos", 3)]},
    {"q": "+ JWT_SECRET = 'changeme123'  # in repo", "cluster": "diff_review",
     "rel": [("r12_ap_jwt_secret_in_repo", 3), ("sec06", 2)]},

    # ── Block H: multilingual_lite (20 new, EN + CS/DE pairs) ─────────────
    # Pattern: 10 EN baseline queries paired with 10 CS-or-DE same-intent
    # queries. Asymmetric grades: localized memory grade=3 if it exists,
    # EN sibling grade=3 (either is acceptable as the "perfect" answer).
    {"q": "N+1 dotazy v ORM smyčkách", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_n1_orm", 3), ("db01", 3), ("db05", 2)]},
    {"q": "N+1 Abfragen in ORM-Schleifen", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_n1_orm", 3), ("db01", 3), ("db05", 2)]},
    {"q": "vždy nastavit timeout u HTTP volání", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_http_timeout", 3), ("api01", 3)]},
    {"q": "HTTP timeout immer setzen", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_http_timeout", 3), ("api01", 3)]},
    {"q": "pytest fixtures místo globálního stavu", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_pytest_fixtures", 3), ("test01", 3)]},
    {"q": "pytest fixtures statt globalem Zustand", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_pytest_fixtures", 3), ("test01", 3)]},
    {"q": "parametrizovat SQL místo f-stringu", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_sql_injection", 3), ("sec09", 3)]},
    {"q": "SQL parametrisieren statt f-String", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_sql_injection", 3), ("sec09", 3)]},
    {"q": "ověřit JWT audience a expiraci", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_jwt_aud", 3), ("sec05", 3)]},
    {"q": "JWT Signatur Audience und Ablauf prüfen", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_jwt_aud", 3), ("sec05", 3)]},
    {"q": "stabilní klíče v React seznamech", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_react_keys", 3), ("fe14", 3)]},
    {"q": "stabile Schlüssel in React-Listen", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_react_keys", 3), ("fe14", 3)]},
    {"q": "spouštět kontejnery jako root je špatně", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_docker_root", 3), ("ops07", 3)]},
    {"q": "Container nicht als root ausführen", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_docker_root", 3), ("ops07", 3)]},
    {"q": "velikost Postgres connection poolu pro FastAPI", "cluster": "multilingual_lite",
     "rel": [("r12_ml_cs_postgres_pool", 3), ("perf02", 3), ("db10", 2)]},
    {"q": "Postgres Connection-Pool-Größe für FastAPI", "cluster": "multilingual_lite",
     "rel": [("r12_ml_de_postgres_pool", 3), ("perf02", 3), ("db10", 2)]},
    # 4 mixed-tongue / multi-lingual diversifiers (EN + CS/DE for clusters
    # that don't have a dedicated localised entry — these stress the
    # tokenizer's recovery on partial localisation)
    {"q": "wie repariere ich Hydration-Mismatch in Next.js", "cluster": "multilingual_lite",
     "rel": [("fe11", 3), ("fe03", 2)]},
    {"q": "jak opravit hydration mismatch v Next.js", "cluster": "multilingual_lite",
     "rel": [("fe11", 3), ("fe03", 2)]},
    {"q": "Argon2id Parameter für Passwort-Hashing", "cluster": "multilingual_lite",
     "rel": [("sec02", 3), ("r12_cs_argon2_params_2025", 3), ("r12_on_argon2_first", 2)]},
    {"q": "parametry Argon2id pro hashování hesel", "cluster": "multilingual_lite",
     "rel": [("sec02", 3), ("r12_cs_argon2_params_2025", 3), ("r12_on_argon2_first", 2)]},

    # ── Block I: lexical_gap_hard (15 new, no token overlap with gold) ────
    # Gold lives in r12_lg_* (and a few existing entries). Query is a
    # paraphrastic intent statement that shares zero tokens with the gold
    # title/content/tags — currently un-addressable without vector retrieval.
    {"q": "don't let stuff hang forever", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_dont_hang_forever", 3), ("api01", 3), ("perf09", 1)]},
    {"q": "stop the upstream from clobbering us when it comes back", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_no_thundering_herd", 3)]},
    {"q": "everyone retries at once and crushes the service", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_no_thundering_herd", 3)]},
    {"q": "don't store passwords like garbage", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_password_storage", 3), ("sec02", 3), ("r12_cs_argon2_params_2025", 2)]},
    {"q": "make sure the second click doesn't double-charge", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_idempotent_post", 3), ("api04", 3)]},
    {"q": "ship the new version to a few people first", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_canary_traffic", 3), ("ops04", 3)]},
    {"q": "follow the breadcrumbs through every service for one user request", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_observability_corr", 3), ("ops02", 2), ("api14", 2), ("ops09", 1)]},
    {"q": "don't blame people in the postmortem", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_blameless_review", 3), ("ops11", 3)]},
    {"q": "give the workload only the keys it actually needs", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_least_authority", 3)]},
    {"q": "build it once and ship that exact thing everywhere", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_immutable_artifacts", 3), ("ops14", 1)]},
    {"q": "small slices ship; big bangs sit", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_paid_for_speed", 3)]},
    {"q": "uptime alone tells you nothing about whether buying still works", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_user_journey_slo", 3), ("ops05", 3)]},
    {"q": "first user pays for everyone else's warmup", "cluster": "lexical_gap_hard",
     "rel": [("r12_lg_warm_state_on_boot", 3), ("perf08", 3), ("ml02", 2)]},
    {"q": "use a real signing scheme, not a checksum", "cluster": "lexical_gap_hard",
     "rel": [("r12_ap_unverified_webhooks", 2), ("sec06", 2), ("sec05", 1)]},
    {"q": "lock-in via tracing vendor — pick the open standard", "cluster": "lexical_gap_hard",
     "rel": [("arch07", 3), ("ops09", 2), ("r12_on_otel_first", 2)]},
]


# ── Sanity check: every labelled id must exist in the corpus ────────────────
_CORPUS_IDS = {c["id"] for c in CORPUS}
_MISSING = sorted(
    {mid for q in QUERIES for mid, _ in q["rel"] if mid not in _CORPUS_IDS}
)
if _MISSING:
    raise AssertionError(
        f"Query labels reference unknown memory ids: {_MISSING}. "
        "Either add the corpus entry or fix the typo before running the eval."
    )


# ── Seeding ─────────────────────────────────────────────────────────────────


def _seed(session, org):
    """Insert the ~255-memory corpus into a fresh DB.

    Notes:
      - ``id`` field is stable (e.g. ``test01``) so query labels stay valid
        across runs and across diffs.
      - ``maturity`` per-memory; we don't pin every memory to VALIDATED any
        more — the maturity_bias@5 metric is meaningless if the corpus is
        flat at one maturity tier.
      - For ``anti_pattern`` rows we also create the AntiPattern child row
        so search.py's intent boost (anti_pattern × secure verbs) fires.
      - For ``decision`` rows we create the Decision child row analogously.
    """
    from memee.storage.models import (
        AntiPattern,
        Decision,
        Memory,
    )

    for c in CORPUS:
        m = Memory(
            id=c["id"],
            type=c["type"],
            maturity=c.get("maturity", "validated"),
            title=c["title"],
            content=c["content"],
            tags=c["tags"],
            confidence_score=0.7,
        )
        session.add(m)
        session.flush()
        if c["type"] == "anti_pattern":
            session.add(
                AntiPattern(
                    memory_id=m.id,
                    severity="medium",
                    trigger=c["title"],
                    consequence=c["content"][:80],
                    alternative="see content",
                )
            )
        elif c["type"] == "decision":
            session.add(
                Decision(
                    memory_id=m.id,
                    chosen=c["title"],
                    alternatives=[],
                    criteria=[],
                )
            )
    session.commit()


def _fresh_db():
    from memee.storage.database import get_engine, get_session, init_db
    from memee.storage.models import Organization

    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    os.environ["MEMEE_DB_PATH"] = str(tmp)
    engine = init_db(get_engine(tmp))
    session = get_session(engine)
    org = Organization(name="eval-org")
    session.add(org)
    session.commit()
    return engine, session, org


# ── Metrics ─────────────────────────────────────────────────────────────────


def _ndcg_at_k(retrieved_ids: list[str], rel_dict: dict[str, int], k: int) -> float:
    """nDCG@k with graded relevance.

    rel_dict maps memory_id → grade in {1, 2, 3}; missing means 0.
    Standard formula: gain = 2**grade - 1 / log2(rank + 2).
    """
    dcg = sum(
        (2 ** rel_dict.get(rid, 0) - 1) / math.log2(i + 2)
        for i, rid in enumerate(retrieved_ids[:k])
    )
    ideal = sorted(rel_dict.values(), reverse=True)
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal[:k]))
    return dcg / idcg if idcg > 0 else 0.0


def _recall_at_k(retrieved_ids: list[str], rel_dict: dict[str, int], k: int) -> float:
    """Recall@k counts any labelled (grade≥1) memory found in the top-k."""
    relevant_ids = {rid for rid, g in rel_dict.items() if g >= 1}
    if not relevant_ids:
        return 0.0
    found = sum(1 for r in retrieved_ids[:k] if r in relevant_ids)
    return found / len(relevant_ids)


def _mrr(retrieved_ids: list[str], rel_dict: dict[str, int]) -> float:
    """MRR using the first labelled (grade≥1) hit."""
    relevant_ids = {rid for rid, g in rel_dict.items() if g >= 1}
    for i, rid in enumerate(retrieved_ids):
        if rid in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def _type_match_precision_at_k(
    retrieved_memories: list, rel_dict: dict[str, int], k: int
) -> float:
    """Precision@k restricted to "type matches dominant gold type".

    Picks the most common ``type`` among gold positives (grade ≥ 1) and
    counts what fraction of the top-k retrieved share that type. If the
    gold is a tie or empty, returns 0.0 (uninformative).
    """
    if not rel_dict:
        return 0.0
    # Iterate the labels list (not a set) so the type-mode is reproducible
    # across runs even when ties exist — set iteration order is hash-based
    # under PYTHONHASHSEED=random and silently drifts the metric.
    types_by_id = {c["id"]: c["type"] for c in CORPUS}
    gold_types = [
        types_by_id[rid] for rid, g in rel_dict.items()
        if g >= 1 and rid in types_by_id
    ]
    if not gold_types:
        return 0.0
    # Mode with deterministic tiebreak (highest count, then alphabetical)
    counts: dict[str, int] = {}
    for t in gold_types:
        counts[t] = counts.get(t, 0) + 1
    dominant = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    if not retrieved_memories:
        return 0.0
    top = retrieved_memories[:k]
    if not top:
        return 0.0
    matches = sum(1 for m in top if m.type == dominant)
    return matches / len(top)


def _maturity_bias_at_k(retrieved_memories: list, k: int) -> float:
    """Mean MATURITY_MULTIPLIER of the top-k retrieved.

    ≥0.85 means we surfaced canon/validated; ≤0.65 means we drifted into
    hypothesis/tested territory. Independent of relevance labels — purely a
    diagnostic on whether the ranker is biased toward mature knowledge.
    """
    if not retrieved_memories:
        return 0.0
    top = retrieved_memories[:k]
    vals = [MATURITY_MULTIPLIER.get(m.maturity, 0.5) for m in top]
    return sum(vals) / len(vals)


# ── Permutation test ────────────────────────────────────────────────────────


def permutation_test(
    scores_a: list[float],
    scores_b: list[float],
    n_iter: int = 10000,
    seed: int = 0,
) -> float:
    """Two-sided paired permutation test on two equal-length score lists.

    Used to compare retrieval rankers (e.g. baseline vs LTR rerank) on the
    same query set: each query produces one paired ``(score_a, score_b)``,
    and the null hypothesis is "the labels A/B are exchangeable per query".

    Returns the two-sided p-value: P(|mean(diff_perm)| ≥ |mean(diff_obs)|).

    Notes:
      - Assumes paired scores (per-query): if lists differ in length we
        raise; the caller's mistake to silently pretend otherwise.
      - 10k iterations is enough to resolve p-values down to ~1e-3.
      - Deterministic with a fixed seed so CI runs are reproducible.
    """
    if len(scores_a) != len(scores_b):
        raise ValueError(
            f"permutation_test: paired scores must match length "
            f"({len(scores_a)} vs {len(scores_b)})"
        )
    if not scores_a:
        return 1.0
    n = len(scores_a)
    diffs = [a - b for a, b in zip(scores_a, scores_b)]
    observed = sum(diffs) / n
    rng = random.Random(seed)
    abs_obs = abs(observed)
    extreme = 0
    for _ in range(n_iter):
        # Per-query sign flip = relabel within each pair
        perm_mean = 0.0
        for d in diffs:
            perm_mean += d if rng.random() < 0.5 else -d
        perm_mean /= n
        if abs(perm_mean) >= abs_obs:
            extreme += 1
    return extreme / n_iter


# ── Eval driver ─────────────────────────────────────────────────────────────


CLUSTER_NAMES = (
    "code_specific",
    "paraphrastic",
    "anti_pattern_intent",
    "onboarding_to_stack",
    "diff_review",
    "multilingual_lite",
    "lexical_gap_hard",
)


def evaluate(
    use_vectors: bool = False,
    cluster: str | None = None,
) -> dict:
    """Run the full eval and return a metrics dict.

    BM25-only is the default — this harness exists to detect ranker swings,
    and the BM25-only path is reproducible without the embedding model
    (which is offline-gated in CI). Pass ``use_vectors=True`` to exercise
    the hybrid path; that requires the sentence-transformers model cached
    locally.

    R12 expansion: every query carries a ``cluster`` tag (one of the seven
    difficulty clusters in ``CLUSTER_NAMES``). Per-cluster macro metrics are
    always computed alongside the full-set macros. Pass ``cluster="<name>"``
    to restrict the run to one cluster — useful for A/B testing a ranker
    change against the cluster it's expected to move.
    """
    from memee.engine.search import search_memories

    if cluster is not None and cluster not in CLUSTER_NAMES:
        raise ValueError(
            f"Unknown cluster {cluster!r}. Known: {CLUSTER_NAMES}"
        )

    engine, session, org = _fresh_db()
    _seed(session, org)

    queries = (
        [q for q in QUERIES if q.get("cluster") == cluster]
        if cluster is not None
        else list(QUERIES)
    )
    if not queries:
        session.close()
        return {
            "n_queries": 0,
            "n_corpus": len(CORPUS),
            "ranker": "hybrid" if use_vectors else "bm25_only",
            "cluster_filter": cluster,
            "per_query": [],
        }

    per_query = []
    ndcg10s: list[float] = []
    recall5s: list[float] = []
    recall10s: list[float] = []
    mrrs: list[float] = []
    type_p5s: list[float] = []
    mat_b5s: list[float] = []

    # Per-cluster score lists keyed by cluster name. Used for both per-cluster
    # macros and per-cluster permutation tests when comparing against a
    # baseline. Default-init so empty clusters report as 0.0 instead of NaN.
    per_cluster_scores: dict[str, dict[str, list[float]]] = {
        name: {"ndcg10": [], "recall5": [], "recall10": [], "mrr": [],
               "type_p5": [], "mat_b5": []}
        for name in CLUSTER_NAMES
    }

    for sample in queries:
        rel_dict: dict[str, int] = {mid: g for mid, g in sample["rel"]}
        results = search_memories(
            session, sample["q"], limit=10, use_vectors=use_vectors
        )
        retrieved_ids = [r["memory"].id for r in results]
        retrieved_memories = [r["memory"] for r in results]

        ndcg10 = _ndcg_at_k(retrieved_ids, rel_dict, 10)
        r5 = _recall_at_k(retrieved_ids, rel_dict, 5)
        r10 = _recall_at_k(retrieved_ids, rel_dict, 10)
        mrr = _mrr(retrieved_ids, rel_dict)
        type_p5 = _type_match_precision_at_k(retrieved_memories, rel_dict, 5)
        mat_b5 = _maturity_bias_at_k(retrieved_memories, 5)

        ndcg10s.append(ndcg10)
        recall5s.append(r5)
        recall10s.append(r10)
        mrrs.append(mrr)
        type_p5s.append(type_p5)
        mat_b5s.append(mat_b5)

        cl = sample.get("cluster")
        if cl in per_cluster_scores:
            per_cluster_scores[cl]["ndcg10"].append(ndcg10)
            per_cluster_scores[cl]["recall5"].append(r5)
            per_cluster_scores[cl]["recall10"].append(r10)
            per_cluster_scores[cl]["mrr"].append(mrr)
            per_cluster_scores[cl]["type_p5"].append(type_p5)
            per_cluster_scores[cl]["mat_b5"].append(mat_b5)

        per_query.append({
            "q": sample["q"],
            "cluster": cl,
            "rel": [list(t) for t in sample["rel"]],
            "retrieved_top5": retrieved_ids[:5],
            "ndcg10": round(ndcg10, 4),
            "recall5": round(r5, 4),
            "recall10": round(r10, 4),
            "mrr": round(mrr, 4),
            "type_p5": round(type_p5, 4),
            "mat_b5": round(mat_b5, 4),
        })

    session.close()
    n = len(queries)

    # Per-cluster macro block — flat dict so it survives JSON round-trips
    # and the `--compare-with` differ doesn't have to know about nesting.
    per_cluster: dict[str, dict] = {}
    for name in CLUSTER_NAMES:
        bucket = per_cluster_scores[name]
        bn = len(bucket["ndcg10"])
        if bn == 0:
            per_cluster[name] = {"n": 0}
            continue
        per_cluster[name] = {
            "n": bn,
            "ndcg10": round(sum(bucket["ndcg10"]) / bn, 4),
            "recall5": round(sum(bucket["recall5"]) / bn, 4),
            "recall10": round(sum(bucket["recall10"]) / bn, 4),
            "mrr": round(sum(bucket["mrr"]) / bn, 4),
            "type_p5": round(sum(bucket["type_p5"]) / bn, 4),
            "mat_b5": round(sum(bucket["mat_b5"]) / bn, 4),
            # raw per-query nDCG list; keeps permutation_test reproducible
            # without re-running the eval. Stripped from the printed summary.
            "ndcg10_per_query": [round(s, 4) for s in bucket["ndcg10"]],
        }

    return {
        "n_queries": n,
        "n_corpus": len(CORPUS),
        "ranker": "hybrid" if use_vectors else "bm25_only",
        "cluster_filter": cluster,
        "macro_ndcg10": round(sum(ndcg10s) / n, 4),
        "macro_recall5": round(sum(recall5s) / n, 4),
        "macro_recall10": round(sum(recall10s) / n, 4),
        "macro_mrr": round(sum(mrrs) / n, 4),
        "macro_type_p5": round(sum(type_p5s) / n, 4),
        "macro_mat_b5": round(sum(mat_b5s) / n, 4),
        "per_cluster": per_cluster,
        "per_query": per_query,
    }


def cluster_permutation_tests(
    current: dict,
    baseline: dict,
    n_iter: int = 10000,
    seed: int = 0,
) -> dict[str, dict]:
    """Run paired permutation tests per cluster on saved eval runs.

    Both ``current`` and ``baseline`` are dicts produced by ``evaluate()``
    (or the on-disk JSON the CLI writes via ``--save``). For each cluster
    that has the same number of queries in both runs, returns ::

        {
            cluster: {
                "n": int,
                "delta_ndcg10": float,   # current - baseline
                "p_value": float,        # two-sided
            },
            ...
        }

    Clusters with mismatched query counts (eval set drift) are reported with
    ``"skipped": "n_mismatch"`` so the caller doesn't silently get the wrong
    test. Callers will typically only act on results below p<0.05.
    """
    out: dict[str, dict] = {}
    cur_per = current.get("per_cluster", {})
    base_per = baseline.get("per_cluster", {})
    for name in CLUSTER_NAMES:
        cur = cur_per.get(name, {})
        base = base_per.get(name, {})
        a = cur.get("ndcg10_per_query")
        b = base.get("ndcg10_per_query")
        if a is None or b is None:
            out[name] = {"skipped": "missing_per_query"}
            continue
        if len(a) != len(b):
            out[name] = {"skipped": "n_mismatch", "n_current": len(a), "n_baseline": len(b)}
            continue
        if not a:
            out[name] = {"skipped": "empty"}
            continue
        p = permutation_test(a, b, n_iter=n_iter, seed=seed)
        out[name] = {
            "n": len(a),
            "delta_ndcg10": round(sum(a) / len(a) - sum(b) / len(b), 4),
            "p_value": round(p, 4),
        }
    return out


# ── CLI: --save and --compare-with ──────────────────────────────────────────


_BENCH_DIR = ROOT / ".bench"


def _save_run(label: str, metrics: dict) -> Path:
    _BENCH_DIR.mkdir(exist_ok=True)
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in label)
    out = _BENCH_DIR / f"eval_{safe}.json"
    out.write_text(json.dumps(metrics, indent=2))
    return out


def _compare_runs(current: dict, baseline_path: Path) -> str:
    baseline = json.loads(baseline_path.read_text())
    keys = [
        "macro_ndcg10",
        "macro_recall5",
        "macro_recall10",
        "macro_mrr",
        "macro_type_p5",
        "macro_mat_b5",
    ]
    lines = [
        f"Compare: current vs {baseline_path.name}",
        f"{'metric':<22} {'baseline':>10} {'current':>10} {'delta':>10}",
        "-" * 56,
    ]
    for k in keys:
        b = baseline.get(k, 0.0)
        c = current.get(k, 0.0)
        d = c - b
        sign = "+" if d >= 0 else ""
        lines.append(
            f"{k:<22} {b:>10.4f} {c:>10.4f} {sign}{d:>9.4f}"
        )
    return "\n".join(lines)


def _parse_arg(name: str, default=None) -> str | None:
    """Tiny opt parser (avoids argparse to keep this file dependency-free).

    Supports both ``--name value`` and ``--name=value``.
    """
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(f"{name}="):
            return a.split("=", 1)[1]
    return default


def _print_summary(res: dict) -> None:
    """Pretty-print the macro and per-cluster headlines.

    Skipped fields:
      - ``per_query``: too verbose for a one-shot run
      - ``per_cluster.<name>.ndcg10_per_query``: kept in the saved JSON for
        downstream permutation tests, but noisy in the console summary
    """
    summary = {k: v for k, v in res.items() if k != "per_query"}
    if "per_cluster" in summary:
        summary["per_cluster"] = {
            name: {k: v for k, v in body.items() if k != "ndcg10_per_query"}
            for name, body in summary["per_cluster"].items()
        }
    print(json.dumps(summary, indent=2))


def _print_cluster_table(res: dict) -> None:
    """Per-cluster nDCG@10 / Recall@5 / MRR table for at-a-glance review."""
    pc = res.get("per_cluster") or {}
    if not pc:
        return
    rows = [(name, pc.get(name, {})) for name in CLUSTER_NAMES]
    print()
    print(f"{'cluster':<22} {'n':>4} {'nDCG@10':>9} {'R@5':>7} {'MRR':>7} {'mat_b5':>8}")
    print("-" * 62)
    for name, body in rows:
        if body.get("n", 0) == 0:
            print(f"{name:<22} {'0':>4} {'-':>9} {'-':>7} {'-':>7} {'-':>8}")
            continue
        print(
            f"{name:<22} {body['n']:>4} {body['ndcg10']:>9.4f} "
            f"{body['recall5']:>7.4f} {body['mrr']:>7.4f} {body['mat_b5']:>8.4f}"
        )


if __name__ == "__main__":
    use_vectors = "--vectors" in sys.argv
    cluster = _parse_arg("--cluster")
    res = evaluate(use_vectors=use_vectors, cluster=cluster)

    _print_summary(res)
    _print_cluster_table(res)

    save_label = _parse_arg("--save")
    if save_label:
        out_path = _save_run(save_label, res)
        print(f"\nSaved: {out_path}")

    compare_path = _parse_arg("--compare-with")
    if compare_path:
        p = Path(compare_path)
        if not p.exists():
            print(f"\n[compare-with] file not found: {p}", file=sys.stderr)
            sys.exit(2)
        print()
        print(_compare_runs(res, p))
        # Per-cluster significance vs the baseline. We always run 10k
        # permutations — this stays under 1s even at 200+ queries — and
        # report Δ + two-sided p so a future ranker change can claim
        # "ΔnDCG@10 +0.04 on paraphrastic, p=0.03" without manual stats.
        baseline = json.loads(p.read_text())
        cluster_stats = cluster_permutation_tests(res, baseline, n_iter=10000)
        print()
        print(f"{'cluster':<22} {'n':>4} {'Δ nDCG@10':>11} {'p':>8}")
        print("-" * 50)
        for name in CLUSTER_NAMES:
            row = cluster_stats.get(name, {})
            if "skipped" in row:
                print(f"{name:<22} {'-':>4} {'-':>11} {row['skipped']:>8}")
                continue
            print(
                f"{name:<22} {row.get('n', 0):>4} "
                f"{row.get('delta_ndcg10', 0.0):>+11.4f} "
                f"{row.get('p_value', 1.0):>8.4f}"
            )

    if "--verbose" in sys.argv:
        print("\nPer-query:")
        for p in res["per_query"]:
            cl = p.get("cluster", "-")
            print(f"  [{cl}] {p['q']}")
            print(f"    rel={p['rel']}  retrieved_top5={p['retrieved_top5']}")
            print(
                f"    nDCG@10={p['ndcg10']}  Recall@5={p['recall5']}  "
                f"MRR={p['mrr']}  type_p5={p['type_p5']}  mat_b5={p['mat_b5']}"
            )
