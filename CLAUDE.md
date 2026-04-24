# Memee — Institutional Memory for AI Agent Companies

**Your agents forget. Memee doesn't.**

Cross-project, cross-model organizational memory. Patterns learned in one project spread to others. Mistakes are recorded and prevented org-wide. Knowledge matures through confidence scoring. Smart routing delivers only relevant knowledge — not a dump.

## Architecture

```
┌─────────────── AGENTS (Claude, GPT, Gemini, Llama) ───────────────┐
│  Session hook / MCP tools / CLI / REST API                         │
├───────────────────────────────────────────────────────────────────┤
│                         MEMEE ENGINE                               │
│                                                                    │
│  PUSH (knowledge → agent):          PULL (agent → knowledge):     │
│    router.py    — smart briefing      search.py  — hybrid BM25+vec│
│    briefing.py  — CLAUDE.md inject    review.py  — git diff check │
│    feedback.py  — post-task loop      predictive — AP scan        │
│                                                                    │
│  QUALITY:                           LEARNING:                     │
│    quality_gate — validate+dedup      confidence — adaptive scoring│
│    plugins.py   — memee-team hooks    lifecycle  — aging+promote  │
│    models.py    — model family detect dream.py   — nightly process│
│                                                                    │
│  GROWTH:                            MEASUREMENT:                  │
│    propagation  — cross-project push  impact.py  — real ROI track │
│    inheritance  — onboard from similar tokens.py  — savings calc  │
│    research.py  — autoresearch engine benchmarks  — OrgMemEval    │
│                                                                    │
├───────────────────────────────────────────────────────────────────┤
│  SQLite + FTS5 + Embeddings (384-dim) | ~/.memee/memee.db          │
└───────────────────────────────────────────────────────────────────┘
```

**Stack:** Python 3.11+ · SQLAlchemy 2.0 · SQLite · Click · FastMCP · FastAPI · sentence-transformers

## Quick Commands

```bash
# Setup
pip install -e ".[dev]"
memee setup                       # Interactive wizard
memee doctor                      # Health check + auto-configure AI tools

# Smart briefing (PUSH — tells agent what it needs)
memee brief --task "write tests"  # Token-budgeted, task-routed
memee inject --project .          # Write org knowledge into CLAUDE.md

# Record knowledge
memee record pattern "title" --tags python,api -c "content with WHY and WHEN"
memee warn "title" --trigger "when" --consequence "what" --severity high
memee decide "X" --over "Y,Z" --reason "why"

# Search + check (PULL — agent asks)
memee search "query"
memee check "what I'm about to do"
memee suggest --context "my current task"

# Intelligence
memee propagate                   # Push patterns cross-project
memee dream                       # Nightly: connect, find contradictions, promote
memee review -                    # Pipe git diff for institutional review
memee embed                       # Generate vector embeddings

# Autoresearch
memee research start "goal" --metric acc --verify "pytest ..."
memee research run <id>           # Execute one iteration
memee research status             # Trajectory + keep rate
memee research meta               # Meta-learning insights

# Analytics
memee status                      # Learning dashboard
memee benchmark                   # OrgMemEval: 93.8/100
memee dashboard                   # Web UI at http://127.0.0.1:7878
memee demo --weeks 52             # Generate demo data

# CMAM bridge — push canon to Claude Managed Agents Memory
memee cmam sync                   # Canon + critical APs → /mnt/memory/ layout
memee cmam sync --dry-run         # Preview without writing
memee cmam status                 # Store size, count, headroom
```

## Engine Modules (16) + Adapters

| Module | Purpose | Impact |
|--------|---------|--------|
| `confidence.py` | Adaptive scoring + maturity lifecycle | Core |
| `search.py` | Hybrid BM25 + vector (sentence-transformers) | Core |
| `lifecycle.py` | Aging, auto-archive 60d, invalidation ratio deprecation | Core |
| `quality_gate.py` | Validate + dedup + source classify + quality score | Core |
| `router.py` | Smart task-aware briefing, 500 token budget, query expansion | Core |
| `briefing.py` | CLAUDE.md injection, pre-task briefing generation | PUSH |
| `feedback.py` | Post-task review, teaching effectiveness tracking | PUSH |
| `propagation.py` | Cross-project auto-push + expanded tag inference | +68.8% IQ |
| `predictive.py` | Anti-pattern push (critical → ALL projects) | +36.6% IQ |
| `dream.py` | Nightly: propagate + connect + contradictions + promote | +24.8% IQ |
| `review.py` | Git diff scan vs anti-pattern + pattern DB | +11.4% IQ |
| `inheritance.py` | Stack+tag similarity, new project onboarding | +9.5% IQ |
| `research.py` | Autoresearch: create/run/track/meta-learn | Autoresearch |
| `embeddings.py` | sentence-transformers all-MiniLM-L6-v2 (384-dim) | Search |
| `models.py` | Model family detection (8 families), diversity bonus | Multi-model |
| `impact.py` | Measurable ROI: time saved, iterations saved, mistakes avoided | Measurement |
| `tokens.py` | Token savings calculator per model pricing | Measurement |
| `plugins.py` | Hook registry for `memee-team` plugin | Extension point |
| `telemetry.py` | Retrieval event log (hit@1, hit@3, acceptance rate) | Quality metrics |
| `adapters/cmam.py` | Claude Managed Agents Memory bridge (canon → CMAM) | Delivery |

*Note:* `scoping.py` (personal → team → org, promotion rules, onboarding) used
to live here. It has been extracted to the proprietary `memee-team` package
alongside `User`/`Team` models, SSO, audit log, and licence verification. OSS
`memee` is a single-user product; multi-user features live in `memee-team`.

## Confidence Scoring

```
New memory: 0.5 (max uncertainty)

Validation bonuses (stackable):
  Same project, same model:        ×1.0 (base 0.08)
  Same project, different model:   ×1.3 (model diversity)
  Different project, same model:   ×1.5 (cross-project)
  Different project + model:       ×1.95 (combined max)

Invalidation: -0.12 × current (no model bonus)
Uncertainty:  1 / sqrt(evidence + 1)

Maturity: hypothesis → tested (1 app) → validated (0.7, 3 proj) → canon (0.85, 5 proj, 10 val)
Auto-deprecate: conf < 0.2 after 3 apps, OR invalidation ratio > 60%, OR unused 60 days

Source multiplier: human ×1.2, llm ×0.8, import ×0.6
```

## Smart Router (PUSH)

```
NOT: dump 500 patterns into CLAUDE.md (14,550 tokens, $27K/year)
BUT: route 5-7 relevant ones per task (500 tokens, $1.1K/year = 96% savings)

Layer 0: CRITICAL anti-patterns (always, ~100 tokens)
Layer 1: Search-routed by task description (BM25+vector, ~300 tokens)
Footer:  Token count + search hint (~50 tokens)

"write unit tests" → testing + security patterns
"optimize database" → pooling + indexing + N+1 patterns
"SEO meta tags" → SEO + content optimization patterns
"GDPR audit" → compliance + consent + data deletion

60+ query expansion patterns across engineering, marketing, product,
design, data, operations. No hardcoded domains — search-based routing.
```

## Quality Gate

```
Pipeline: validate → dedup → source classify → quality score

Validate:  title ≥10 chars, content ≥15 chars, content ≠ title, ≥1 tag,
           rejects TODOs/meeting notes/garbage
Dedup:     SequenceMatcher > 85% → merge into existing
Source:    human ×1.2, llm ×0.8, import ×0.6
Quality:   heuristic 1-5 (title, content depth, WHY/WHEN context, tags, actionability)
           team/org scope: quality < 2.5 = flagged
```

## Packages — OSS ↔ paid split

Memee ships as two packages, with clear licence separation:

| Package | Licence | What it adds |
|---|---|---|
| **`memee`** (this repo) | MIT | Full single-user product: every engine module, MCP server, CLI, CMAM adapter, dashboard. No users, no teams, no scope enforcement. |
| **`memee-team`** (private repo, licence-gated) | Proprietary (EULA) | `User` + `Team` SQLAlchemy models, `scoping.py` engine (personal → team → org promotion), SSO (SAML/OIDC), audit log export, RBAC, multi-user dashboard auth, licence key verification. |

`memee-team` plugs into OSS via `memee.plugins` hooks
(`current_user_id`, `visible_memories`, `promote`, `can_promote`, `on_record`).
Without it installed, OSS runs as single-user and promotion raises
`LicenseRequiredError` with an upgrade message.

Pricing (honoured on memee.eu):

```
Free / OSS (MIT):       $0 forever, single user, every AI feature
Team (EULA):            $49 / month flat, up to 15 seats, annual
                         + multi-user scope + SSO + audit + Postgres
Enterprise:             from $12k / year, unlimited seats, SOC 2,
                         air-gap, SLA, custom MSA
```

Pricing model reflects "Memee is memory, not model" — flat per-team
(like Supabase, Vercel, Plausible), not per-seat (like Copilot, Cursor).
Value scales sublinearly with headcount: one canon serves the whole team.

## MCP Tools (23)

Core: memory_record, memory_search, memory_suggest, memory_validate,
memory_invalidate, decision_record, antipattern_record, antipattern_check

Intelligence: propagate_patterns, predict_warnings, inherit_knowledge,
run_dream, review_code, get_briefing, post_task_feedback

Research: research_create, research_log, research_status, research_meta,
research_complete

Analytics: learning_status, canon_list

Delivery: sync_to_cmam (push canon to Claude Managed Agents Memory)

## CMAM Bridge (Claude Managed Agents Memory)

Anthropic's managed memory is a filesystem-style store at `/mnt/memory/` inside
a Claude agent container. It's a dumb store — Memee stays the brain.

```
Memee (multi-model intelligence):        CMAM (Claude-native delivery):
  confidence + maturity                    /canon/patterns/<slug>.md
  quality gate + dedup                     /canon/lessons/<slug>.md
  cross-project propagation         ──→    /warnings/critical/<slug>.md
  token-budgeted routing                   /warnings/high/<slug>.md
  multi-model validation                   /decisions/<slug>.md
                                           /_index.md
```

Sync triggers: CANON maturity OR critical anti-pattern (severity=critical
propagates regardless of maturity). Secrets auto-redacted. Content >100 KB
auto-chunked into `.part-N.md`. Store caps enforced (80 MB soft, 95 MB hard,
1600/1900 count thresholds).

```bash
memee cmam sync --backend fs --local-root ~/.memee/cmam/my-store
memee cmam sync --backend api --store-id my-org          # needs ANTHROPIC_API_KEY
memee cmam sync --dry-run
memee cmam status
```

MCP tool `sync_to_cmam` lets agents trigger the push themselves.

## Benchmarks

**OrgMemEval v1.0:** 93.8/100 (competitors: 2.3/100)
- Propagation 100% | Avoidance 100% | Maturity 89% | Onboarding 100%
- Recovery 100% | Calibration 83% | Synthesis 82% | Research 92%

**Competitive:** Memee 6.5 | Mem0 3.5 | Zep 2.3 | Letta 1.3 | MemPalace 0.9

**Performance:** 11K inserts/s | 7.6ms BM25 | 113ms hybrid search | 10K conf updates/s

**Impact (A/B test, 7 tasks):**
- Time: 1470min → 430min (-71%)
- Iterations: 43 → 15 (-65%)
- Mistakes: 14 → 0 (100% prevented)
- Quality: 56% → 93% (+36pp)
- ROI: 10.7x

**GigaCorp (18 months, 100 agents, 200 projects):**
- Incidents: 12/mo → 3/mo (75% reduction)
- Token savings: 501M tokens/year ($3,911)
- Total ROI: 7x ($16,268 saved / $2,388 cost)

## Key Files

| File | Purpose |
|------|---------|
| `src/memee/cli.py` | 25+ Click commands (incl. `cmam sync`/`cmam status`) |
| `src/memee/mcp_server.py` | 24 MCP tools |
| `src/memee/adapters/cmam.py` | Claude Managed Agents Memory bridge |
| `src/memee/storage/models.py` | 15 SQLAlchemy models |
| `src/memee/storage/database.py` | DB init, FTS5, WAL mode |
| `src/memee/api/routes/dashboard.py` | Chart.js dashboard |
| `src/memee/api/routes/api_v1.py` | REST API (12+ endpoints) |
| `src/memee/installer.py` | Interactive setup wizard |
| `src/memee/doctor.py` | Health check + auto-configure AI tools |
| `src/memee/demo.py` | Enterprise demo data generator |
| `src/memee/benchmarks/orgmemeval.py` | OrgMemEval (8 scenarios) |
| `src/memee/config.py` | Pydantic settings (MEMEE_ env vars) |

## Tests

```bash
pytest tests/ -v   # 201 tests, ~67s
```

Simulation tests: test_company_simulation (NovaTech 6mo), test_enterprise (TechCorp 52wk),
test_megacorp (100 proj, hallucination defense), test_gigacorp (200 proj, 18 months),
test_benchmarks (competitive), test_blind_spots (14 failure modes),
test_real_impact (A/B with/without), test_perf_simulation (9 scenarios)

## Project Stats

- 33 commits on feat/initial-setup
- 63 Python files, 18,899 lines of code
- 201 tests passing
- 16 engine modules + CMAM adapter, 24 MCP tools, 12+ API endpoints (GET-only dashboard API)
- MIT licence (OSS `memee`), proprietary EULA for `memee-team`
