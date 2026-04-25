# Gamechangers — what each one does, in depth

A companion to [`docs/roadmap.md`](roadmap.md). The roadmap names six
items as gamechangers and gives the headline pitch. This file goes
much deeper for each one: the mechanism in code, three concrete
before/after user scenarios, what the product becomes, every
measurable change, every honest trade-off, the competitive landscape,
the rollout plan, the edge cases.

The frame: a gamechanger isn't a feature that improves a metric. It's
a feature that **changes which metric matters** — that lets us tell a
different story about what Memee is for. We have six. Three I'm
confident about. Three I have lower confidence in and say so.

If you're skimming, the headline ranking by impact-per-engineer-week:

1. ★★★ Cross-encoder default-on (already shipped opt-in)
2. ★★ Neuro-symbolic review (highest measured headroom)
3. ★★★ Evidence graph as canon ledger (deepest narrative shift)
4. ★★ LTR + counterfactual logging
5. ★ Expected-value router
6. ★ Privacy-first embeddings + per-scope encryption

The numbers cited for #1 and the cluster baselines for #4 are
**measured** against current `main`. Everything else is honestly
estimated; I mark predictions explicitly.

---

# ★★★ 1. Cross-encoder rerank — default-on, model bundled

## 1.1 What it does, mechanically

A bi-encoder (today's vector retriever in `_vector_topk`) computes
two embeddings independently: one for the query, one for each candidate
memory. Then we score by cosine. This is fast — one matmul over a
cached corpus matrix — but it loses signal because the two encodings
never see each other.

A cross-encoder takes `(query, candidate)` as a single input and
returns a single relevance score. Slower (one inference per pair, no
caching) but it catches paraphrase, intent, and out-of-vocabulary
signals that BM25 + bi-encoder vectors miss together. It's the
canonical post-2023 production setup for any IR system that's not
purely keyword-based.

We already shipped the wiring in R14 #2. `engine/reranker.py`
implements a `CrossEncoderReranker` class with lazy module-level model
caching (threading.Lock, `_LOAD_FAILED` short-circuit). Stage 5a in
`search_memories` runs before any LTR rerank, takes the top-30 RRF
candidates, scores each `(query, title + content[:200])` pair, and
re-orders them.

Today: opt-in only. The user has to:

```bash
pip install memee[rerank]
export MEMEE_RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

Gamechanger move: **default on when the optional dep is installed**,
ship the model in a lazy-load wheel (`memee[rerank-bundled]`),
auto-enable the env var when the wheel is detected.

## 1.2 The mechanism in code — what changes

Three files:

- **`pyproject.toml`** — add `[rerank-bundled]` extra that ships the
  model file (~80 MB compressed) alongside `sentence-transformers`,
  pinned to one specific cross-encoder version so we don't get
  surprise model updates.
- **`engine/reranker.py`** — the env-var gate flips. Today
  `is_enabled()` returns True only if `MEMEE_RERANK_MODEL` is set.
  After: returns True if **either** the env var is set **or**
  `sentence-transformers` is importable AND a bundled model file
  exists at the expected path.
- **`engine/search.py`** — no change. The stage 5a wire already
  exists; we just exercise it for more users.

About 30 lines of diff total. The risky part isn't the code, it's
the packaging.

## 1.3 Three concrete scenarios

### Scenario A: "fix the hang in our request handler"

A developer is debugging a production hang. They run:

```bash
$ memee search "fix the hang in our request handler"
```

**Today** (BM25-only default path):

```
1. [VAL|65%] Always wrap async tasks in timeout                  Score: 0.62
2. [CAN|90%] Use connection pool with max 10                     Score: 0.59
3. [VAL|72%] Restart workers after deadlock                      Score: 0.51
4. [CAN|94%] Always set HTTP timeout on outbound requests        Score: 0.48  ← actually relevant
5. [VAL|68%] Profile before optimizing                           Score: 0.41
```

The relevant memory is rank 4. The query had no overlap with the
canonical anti-pattern's title ("set timeout on outbound HTTP
requests") because the developer used the word "hang" — present in
the **content** of the relevant memory ("Unbounded `requests.get`
hangs the worker forever") but not the title. BM25 ranks the
title-token-overlap matches above the content match.

**After** (default cross-encoder):

```
1. [CAN|94%] Always set HTTP timeout on outbound requests        Score: 0.93
2. [VAL|65%] Always wrap async tasks in timeout                  Score: 0.71
3. [VAL|72%] Restart workers after deadlock                      Score: 0.62
4. [CAN|90%] Use connection pool with max 10                     Score: 0.55
5. [VAL|68%] Profile before optimizing                           Score: 0.38
```

Cross-encoder reads the full pair `(query, title + content)`. It
sees that "hang in our request handler" matches the content
"Unbounded requests.get hangs the worker forever" semantically, even
though no token in the query appears in the title. The relevant
memory is rank 1.

The user gets to the right answer in one click instead of scrolling
to position 4.

### Scenario B: agent briefing for "I'm new to Postgres, what should I know"

The MCP `memory_search` tool is what the agent calls. Today the agent
gets back a JSON list ordered by RRF score; with cross-encoder, the
list is reordered.

**Today**, top-3 returned memories for that query:

```json
[
  {"id": "p3", "title": "Use composite indexes for time-bounded scans", "score": 0.71},
  {"id": "p1", "title": "WAL mode for concurrent SQLite writers", "score": 0.68},
  {"id": "p7", "title": "Postgres 15 setup with shared_buffers tuning", "score": 0.65}
]
```

The first two are *technically* about Postgres-adjacent topics but
neither actually helps a beginner. The third — the only one a
beginner would want first — is rank 3.

**After**:

```json
[
  {"id": "p7", "title": "Postgres 15 setup with shared_buffers tuning", "score": 0.94},
  {"id": "p15", "title": "Connection pool sizing for Postgres", "score": 0.81},
  {"id": "p3", "title": "Use composite indexes for time-bounded scans", "score": 0.69}
]
```

The cross-encoder understands that "I'm new to Postgres, what should I
know" is asking for **introductory canon**, not for any random
Postgres-tagged memory. It promotes setup-pattern over composite-
index because the latter assumes a working Postgres install.

Measured: this is the `onboarding_to_stack` cluster. n=25, BM25
baseline 0.6605, with cross-encoder 0.7729 — **+0.1124 nDCG (p=0.03)**.

### Scenario C: dashboard search by an audit user

A compliance officer reviewing policy adherence:

```
search box: "where is our HTTP timeout policy"
```

**Today**: returns 8 results, the policy-defining memory is rank 5
(BM25 weighting hits "HTTP" in lots of unrelated content first).

**After**: rank 1, with the supporting evidence chain visible
because the cross-encoder understood "policy" maps to the canon-
maturity row, not the in-progress hypothesis.

The compliance UX shifts from "list of vaguely-related memories" to
"the policy + evidence chain." That's a different mental model.

## 1.4 What the product becomes

Today's README has to qualify:

> "Hybrid retrieval — BM25 + vectors with optional cross-encoder
> rerank. If you `pip install memee[rerank]` and set the env var, you
> get +5 nDCG points on hard queries."

After: the qualifier disappears.

> "Hybrid retrieval that ranks like a paid IR product. Out of the box."

The difference is whether the marketing claim has an asterisk. Same
engineering team, different positioning.

The competitive comparison also flips:

| product | default ranker quality | dep cost |
|---|---|---|
| Mem0 | bi-encoder semantic search | 100MB+ |
| Zep | bi-encoder + LLM rerank (slow, expensive) | API call per search |
| Letta | bi-encoder | 100MB+ |
| Memee today (default) | BM25 + bi-encoder via RRF | 250MB if vectors enabled |
| Memee with #1 shipped | BM25 + bi-encoder + cross-encoder via RRF + 5a rerank | ~330MB if rerank enabled |

The 80 MB difference between "vectors" and "vectors + cross-encoder
rerank" is the price of the second-tier ranking signal. For users on
laptops or CI runners, that's negligible; for users on edge devices,
they'll keep `[rerank]` off.

## 1.5 Measurable improvements

| metric | BM25-only default | + cross-encoder | Δ | p |
|---|---:|---:|---:|---:|
| macro nDCG@10 | 0.7273 | 0.7628 | +0.0355 | 0.0002 |
| macro Recall@5 | 0.5701 | 0.5950 | +0.0249 | — |
| macro Recall@10 | 0.6292 | 0.6477 | +0.0185 | — |
| macro MRR | 0.8277 | 0.8676 | +0.0399 | — |

Per-cluster the lift concentrates where lexical matching fails:

| cluster | n | BM25-only | + rerank | Δ | p |
|---|---:|---:|---:|---:|---:|
| onboarding_to_stack | 25 | 0.6605 | 0.7729 | **+0.1124** | 0.03 |
| diff_review | 30 | 0.5557 | 0.6192 | **+0.0636** | 0.03 |
| paraphrastic | 43 | 0.6795 | 0.7093 | +0.0298 | 0.08 |
| lexical_gap_hard | 15 | 0.7446 | 0.7846 | +0.0400 | n.s. |
| code_specific | 42 | 0.8355 | 0.8497 | +0.0142 | n.s. |
| anti_pattern_intent | 32 | 0.8266 | 0.8194 | -0.0072 | n.s. |
| multilingual_lite | 20 | 0.7724 | 0.7915 | +0.0191 | n.s. |

The fact that anti_pattern_intent moves slightly *negative* is an
honest observation: that cluster is BM25-saturated already (queries
contain the anti-pattern's exact trigger token), and the cross-
encoder occasionally trades position with a runner-up that's
semantically close. The macro p=0.0002 says it's a net win even with
that wobble.

## 1.6 Latency cost — measured, not estimated

The R14 #2 audit measured per-query latency on a 10k-memory corpus,
warm model, MacBook Pro M1:

| stage | p50 | p95 | p99 |
|---|---:|---:|---:|
| BM25-only default path | 1.3 ms | 1.8 ms | 2.5 ms |
| + cross-encoder over top-30 | 41 ms | 78 ms | 115 ms |

Plus a one-time **3-5 second cold start** on the first search of the
process (lazy model load, no eager warm). Mitigation: in long-lived
processes (FastAPI worker, MCP server) the cold-start hits exactly
once.

For agent loops at 1-2 searches per task, +40 ms per search is
invisible. For dashboard / CLI users on a slow disk, it's noticeable
but fine. For embedded uses (e.g. IDE autocomplete) — that's where
you'd keep the env var off.

## 1.7 What we'd lose

- **~250 MB optional dep, +80 MB for the bundled model.** Already
  optional via `[rerank]`; default-on with auto-detect makes the
  install decision visible at `pip install` time.
- **Latency budget.** 40 ms p50 cost. Acceptable for default-on
  *only* if we keep the off switch. Mitigation: env var stays as
  the kill switch (`MEMEE_RERANK_MODEL=` empty disables).
- **Cold start.** First search of a process pays 3-5 s. Mitigation:
  expose a `memee warmup` command that pre-loads the model so
  long-running deployments can warm at boot.
- **English-only model.** ms-marco-MiniLM-L-6-v2 is trained on
  English. The 207q harness has a `multilingual_lite` cluster
  (n=20) — measured Δ=+0.0191 (positive but n.s.). If we have CS /
  DE / other-language users, we'd want a multilingual cross-encoder
  (e.g. `cross-encoder/ms-marco-multilingual-MiniLM-L12-v2`, +200MB).
  Mitigation: `MEMEE_RERANK_MODEL` is configurable; ship the
  English one as default-bundled, multilingual as optional.
- **No improvement on already-strong queries.** code_specific and
  anti_pattern_intent clusters are BM25-saturated; the rerank is
  net-zero or slightly negative. Honest framing: this lifts the
  weak clusters, not the strong ones.

## 1.8 What changes in how users feel about Memee

Today a power user installs `memee`, runs a search, hits a wrong
ranking, and learns to add `tags=` to win. They become a Memee
expert. That's a power-user product.

After: ranking just works. They don't learn the tag trick because
they don't have to. New users don't bounce on rank-4-bad-results.
That's a default-product.

In product-team terms: today's NPS-style question — "would you
recommend Memee to a colleague?" — gets a different answer when
"out of the box, search ranks like a paid IR product."

## 1.9 Why we haven't shipped yet

Two open packaging questions:

1. **Bundle vs lazy-download.** The bundle is the premium UX (offline
   install works, no first-run network), the lazy-download is the
   lighter wheel (~80 MB difference). I lean toward bundle for OSS
   default + lazy for `memee-team` (where install bandwidth is
   negligible). But reasonable people disagree.
2. **Default-on for OSS or only memee-team.** OSS single-user
   installs run on laptops; the latency is fine. The wheel size is
   real. memee-team installs are server-side; bandwidth doesn't
   matter. Easy answer: default-on for memee-team, opt-in for OSS.
   Harder answer: default-on for both because the marketing claim
   matters.

The data says ship it default-on for both. The remaining question
is purely packaging.

**Estimated effort: 1 week** of packaging work (bundle the model
into a wheel, set up the auto-detect, test on macOS / Linux / WSL),
plus one round of UX testing.

---

# ★★★ 2. Evidence graph as canon ledger

## 2.1 What it does, mechanically

R9 already shipped the schema. Two tables matter:

- **`MemoryConnection(source_id, target_id, relationship_type, strength, expires_at)`**
  with relationship_type ∈ {`contradicts`, `supports`, `related_to`,
  `depends_on`, `supersedes`}. The first three are R7-era; the last
  two are R9.
- **`Memory.evidence_chain`** — a JSON list of provenance entries:
  `[{type, ref, timestamp, agent, outcome}]`.

The dream cycle infers connections nightly. R9 added two new
inference passes:

- **`_infer_dependencies`** — strict tag-superset hierarchy + textual
  cue match. A memory whose tags are a strict superset of another's
  AND whose content matches a "requires X" / "prerequisite" pattern
  becomes a `depends_on` edge.
- **`_infer_supersessions`** — full tag-set overlap + (textual cue
  OR confidence gap ≥ 0.3 AND maturity ordering AND invalidation
  ratio ≥ 0.2). Strict because a wrong supersedes hides the wrong
  memory in briefing.

The briefing path **reads** these edges: `_expand_with_dependencies`
prepends predecessors; `_strip_superseded` drops candidates already
superseded by another in the briefing. Lifecycle blocks deprecation
when CANON depends.

What's **not** there yet: a **read surface** that lets a human or an
agent ask the graph questions. Today canon is a flat list of high-
confidence Memory rows. With the ledger, canon becomes a stateful
graph object you can interrogate.

## 2.2 What missing surfaces would look like

### Five new commands

```bash
$ memee why pat-aws-iam-least-privilege
Memory: pat-aws-iam-least-privilege
  Type: pattern, Maturity: canon, Confidence: 0.94
  Validated in 7 projects: prod-api, prod-data, prod-web, …
  Validated by 3 model families: claude-4, gpt-5, gemini-2

Evidence chain (4 entries):
  2025-09-15  human/security@acme.com: "SOC2-CC6.3 finding"
              ref: https://acme.atlassian.net/AUDIT-2025-Q3-finding-CC6.3
  2025-10-02  agent/claude-4-opus:     "applied successfully"
              ref: prod-api/PR-3421
  2025-11-18  agent/gpt-5:             "applied with modification"
              ref: prod-data/PR-892, modification: "added wildcards for s3:*Get"
  2026-01-04  agent/gemini-2:          "applied successfully"
              ref: prod-web/PR-2103

Graph context (3 edges):
  depends_on → pat-aws-iam-resource-format    (your IAM resources must
                                                 be ARN-formatted first)
  supports   ← pat-aws-iam-policy-versioning  (versioning supports this
                                                 by enabling rollback)
  contradicts ← anti-aws-iam-wildcard-allow  (resolved 2025-10-02; the
                                                 wildcard pattern was
                                                 deprecated in favor of
                                                 this one)
```

```bash
$ memee timeline prod-api --topic security
2025-08-01  hyp-ci-secret-scanning           created (hypothesis)
2025-08-22  hyp-ci-secret-scanning           validated (1st)
2025-09-15  pat-aws-iam-least-privilege      created from SOC2 finding
2025-09-29  pat-aws-iam-least-privilege      validated (1st), promoted to tested
2025-10-02  pat-aws-iam-least-privilege      validated (2nd, cross-project)
2025-10-15  hyp-ci-secret-scanning           promoted to canon
2025-11-12  pat-aws-iam-least-privilege      validated (3rd), promoted to canon
2025-12-04  anti-aws-iam-wildcard-allow      created
2025-12-04  anti-aws-iam-wildcard-allow      contradicts ← pat-aws-iam-least-privilege
2026-01-04  pat-aws-iam-least-privilege      validated (5th, cross-model)
```

```bash
$ memee canon-state --scope team --tags security
Canon set: 23 memories
Contradiction-free: 22 memories
Conflicts:
  pat-bucket-policy-explicit-deny ← contradicts ← pat-bucket-policy-default-deny
  → both at canon maturity, neither supersedes the other
  → flagged for human review on 2026-04-12, unresolved
```

```bash
$ memee provenance pat-aws-iam-least-privilege --format json > evidence.json
# JSON dump for legal/audit ingestion
```

```bash
$ memee canon diff --from 2025-Q3 --to 2026-Q1
+ pat-aws-iam-least-privilege         promoted to canon  2025-11-12
+ pat-bucket-policy-default-deny      promoted to canon  2025-12-22
- anti-aws-iam-wildcard-allow         deprecated         2025-12-04
  (replaced by: pat-aws-iam-least-privilege)
```

### Dashboard graph view

A force-directed graph with:
- Nodes coloured by type (pattern/anti-pattern/decision/lesson) and
  sized by `application_count`.
- Edges coloured by relationship_type, with arrow direction.
- Click a node → opens the provenance panel.
- Filter by tag, scope, project.

This is a different *visual product*. Today's dashboard is a list of
recent memories. After: a knowledge map.

### Rule engine: contradiction guard

A new dream phase that fails closed:

```python
def _check_canon_contradictions(session):
    """Find pairs of CANON memories where one contradicts the other.
    Each pair is a knowledge-base inconsistency that an agent might
    apply unpredictably. Flag for human review; do NOT silently pick
    a winner."""
    pairs = session.query(MemoryConnection).filter(
        MemoryConnection.relationship_type == "contradicts",
        # both endpoints in CANON state
    ).join(...).all()
    for p in pairs:
        # write to lifecycle digest; do not auto-resolve
        ...
```

Today if two CANON memories disagree (e.g. "always use connection
pooling" vs "never use connection pooling for serverless") the
ranker happily returns both. The rule engine detects this at dream
time and flags before either reaches an agent.

## 2.3 What the product becomes

This is the deepest narrative shift on the list.

**Today:** Memee is a search engine for organizational memory.
Search returns a ranked list. The user reads the top-K and applies
their judgment.

**After:** Memee is a **claim ledger** with a search engine on top.
Search returns a ranked list **plus** the structural context (what
this memory depends on, what it supersedes, what evidence chain
backs it, when it became canon). The user can ask "why" instead of
just "what".

The buyer changes:

| today's buyer | tomorrow's buyer (additional) |
|---|---|
| engineering manager who wants AI agents to make fewer dumb mistakes | compliance / audit / legal team who needs provenance-tracked record |
| eval criterion: nDCG@10 | eval criterion: "can I trace this rule back to its source?" |
| pricing question: "how much does Memee cost?" | "how much does compliance not-cost when audit asks?" |

The latter buys at a different price point and asks for SOC2 Type
II and a DPA. They don't care about retrieval quality benchmarks.
They care that `memee why pat-soc2-cc6.3` walks back to the original
audit finding from 2025-Q3 with timestamps and approver.

Concrete buyers we currently can't serve:

- **Healthcare**: HIPAA covered entities. They need "show me the
  decision audit trail for every rule that touches PHI access."
- **Finance**: SOX-regulated. They need "evidence of design and
  operating effectiveness" for every control, with timestamped
  changes.
- **Government / public sector**: FedRAMP / IL5 deployments need
  immutable audit logs with separation-of-duties. The ledger is the
  natural fit.

Each of these reads as "regulated industry adoption" — a category
that today buys ServiceNow + Confluence + manual Excel. Memee with
a canon ledger is the AI-native substitute.

## 2.4 Concrete improvements

Before / after for compliance use cases:

| use case | today | after |
|---|---|---|
| "where did this policy come from?" | git blame in Confluence + Slack thread | `memee why <id>` walks evidence chain |
| "show audit trail since last review" | manual diff between exported JSONs | `memee canon diff --from --to` with promotion + supersession + deprecation events |
| "which canon affects HIPAA?" | tag search + manual triage | `memee canon-state --tags hipaa` returns the contradiction-free set |
| "are any canon memories internally inconsistent?" | nobody knows | dream-cycle contradiction guard + dashboard alert |
| "what supports this canon claim?" | author's memory | graph traversal of `supports` edges with confidence-weighted strength |
| "can I delete this project and have its memories cascade-cleared?" | manual deletion + hope nothing else references | graph-aware cascade with explicit retention policy |

Plus the search-ranking improvements when graph context is fed back
into the ranker:

- Memories with **deep evidence chains** (≥3 entries) get a
  confidence boost in briefing — not just because of `confidence_score`
  but because the chain *itself* is a quality signal.
- Memories that are the **terminal node** of a `supersedes` chain
  outrank their predecessors automatically.
- Memories with **active contradictions** to other CANON get
  *suppressed* from agent briefing pending human review (fail
  closed, not fail open).

Estimated nDCG impact from feeding graph back into ranker: +0.01 to
+0.02 on canon-heavy queries. Not a primary goal — the primary goal
is the audit / compliance surface.

## 2.5 Comparable tools — and why none of them is this

| tool | what it does | what it doesn't do |
|---|---|---|
| Confluence | wiki | no inference, no graph, no AI integration |
| Notion | block-based docs | same |
| Roam Research / Logseq | bidirectional links between notes | local-first; no organisational provenance, no AI feedback loop |
| Obsidian | local notes with tags + backlinks | same |
| ConceptNet | general-knowledge graph | not org-specific, read-only |
| Confluence + Sharepoint + Excel triplet (today's compliance default) | manual | very, very manual |

The closest comparable is what compliance teams build *themselves*
in Confluence + Excel with custom workflows. It's manual,
inconsistent, and stale within weeks. Memee's canon ledger
auto-derives the same structure from agent-recorded memories and
keeps it fresh.

## 2.6 What we'd lose

- **No measurable nDCG win.** The graph layer doesn't directly
  improve retrieval (the +0.01-0.02 estimate above is secondary).
  The primary win is on a *different* axis. Honest framing: this
  isn't a ranker upgrade.
- **Surface area explosion.** Five new CLI commands, new MCP tools
  (`graph_traverse`, `canon_state`, `provenance_chain`,
  `canon_diff`), new dashboard view, new audit-export format.
  Each is documentation + tests + UX.
- **Inference precision risk.** The R9 depends_on / supersedes
  inference uses strict gates and hasn't run in production yet. If
  the inference produces bad edges and the ledger surfaces them
  prominently, we erode trust faster than we build it. Mitigation:
  ship the ledger surface but mark inferred edges separately from
  human-recorded edges; require human confirm for `supersedes` to
  affect canon-state.
- **Storage growth.** Today `MemoryConnection` is empty for most
  installs. After: typical org has 3-5× edges per memory at
  steady-state. Storage estimate at 10k memories: ~30k edges, ~3
  MB. Negligible.
- **Maintenance.** Inference rules need tuning per-customer (one
  org's "depends_on" hierarchy might be flatter than another's).
  Mitigation: env-var tunables on the inference thresholds, per-org
  config in `memee-team`.

## 2.7 What changes about how Memee positions

Three concrete shifts:

1. **Pricing tier.** Today free + $49/mo Team + Enterprise.
   Tomorrow: free + $49/mo Team + **Compliance tier** (~$X00/mo;
   includes ledger surface + audit export + retention policy +
   SOC2-aligned access control). Compliance tier closes a different
   sales motion.
2. **Sales positioning.** "AI memory" → "AI memory + audit-ready
   knowledge ledger." Compound product, two value props.
3. **Documentation site.** Today's docs are dev-focused (CLI,
   MCP, API). Compliance buyer needs separate "compliance" section
   with DPA template, retention policy, access-log format. Owner:
   product/legal, not engineering.

## 2.8 Why we haven't shipped yet

Two cycles of dream output need to be observed before we trust the
inference enough to expose it as the primary product surface. The
schema and the inference rules went into R9. We're at zero cycles
in production. Realistic timeline: 4-6 weeks of dream-cycle data
+ manual review of inferred edges before we ship the ledger UI.

**Estimated effort once gating clears: 3-4 weeks** (CLI surfaces +
dashboard graph view + audit export format + contradiction guard).

---

# ★★ 3. LTR + counterfactual logging — closing the learning loop

## 3.1 What it does, mechanically

Today every ranker constant in `search.py` is a number we guessed:

```python
W_BM25 = 0.42
W_VECTOR = 0.30
W_TAGS = 0.20
W_CONFIDENCE = 0.08
RRF_K = 40
TITLE_PHRASE_BOOST = 1.3
INTENT_BOOSTS = [
    ({"test", "tests", "testing"}, "pattern", 1.1),
    ({"secure", "security", "harden"}, "anti_pattern", 1.15),
    ...
]
```

R9 shipped the LTR plumbing — `SearchRankingSnapshot` rows persist
the per-candidate features at search time, `LTRModel` is a registry
of trained models, `MEMEE_LTR_ENABLED=canary` routes 10% of traffic
to a candidate model. `memee ranker train / promote / mine-negatives`
is wired end-to-end. The training code path runs.

What's missing: **data volume** and **counterfactual logging**.

The data volume problem is mechanical — ≥500 accepted SearchEvents
to train pairwise. At ~50-100 searches/week that's 8-12 weeks of
organic traffic.

The counterfactual problem is more interesting. Today when an agent
calls `search_feedback(accepted_memory_id=X, position=2)`, we mark
the memories at positions 0 and 1 as **negative** for training. But:

- The agent only saw 10 results. We have no signal on the other 90 %
  of the corpus — those memories are missing-not-at-random.
- The agent ignored top-1 because of *something* — maybe the title
  was off, maybe they were exploring a second option, maybe they
  found top-1 first and it was wrong, maybe top-1 was already in
  their context and they wanted variety. Position 0 negative is a
  noisy label.
- Same query gets re-issued with different context. Yesterday's
  acceptance signal might not apply today.

Counterfactual logging fixes this: every search logs an *alternative*
ranker's output alongside the production ranker's. When acceptance
data trickles in, we can ask "would the alternative ranker have
shown this same item at the same position?" — that gives us
off-policy evaluation without affecting users.

## 3.2 The training pipeline, in detail

Once we have ≥500 accepted SearchEvent rows:

```bash
$ memee ranker train --version ltr_v1 --output-dir ~/.memee/models
Loading 547 events...
Found 412 events with snapshot data.
Found 156 hard negatives (rejected_top != accepted).
Building feature matrix: 412 query × ~25 candidates = 10,300 rows × 11 features.
Training pairwise lambdarank, 200 boosting rounds...
  iter 50: nDCG@10 = 0.78 (vs heuristic baseline 0.73)
  iter 100: nDCG@10 = 0.82
  iter 200: nDCG@10 = 0.84
Saved model: ~/.memee/models/ranker_ltr_v1.txt (312 KB)
Registered: ltr_models.id = 5e9...  status = candidate

$ memee ranker eval ltr_v1 --on retrieval_eval
Cross-encoder rerank ENABLED (auto-detected)
Running 207-query harness...
Heuristic ranker:    nDCG@10 = 0.7628
LTR candidate:       nDCG@10 = 0.7891 (+0.0263, p = 0.012)
Permutation test:    accept

$ memee ranker promote 5e9
Promoted to production. Rolling out at canary rate 10%.
Telemetry will A/B for 7 days; auto-revert if hit@1 drops > 5%.
```

The pipeline is automated, gated by retrieval_eval, and reversible.

## 3.3 Three concrete scenarios

### Scenario A: same team, three months in

**Day 1**: heuristic ranker; everyone gets the same ranking. nDCG@10
= 0.7628 (BM25 + vector + tag-graph + cross-encoder).

**Week 6**: 612 accepted SearchEvents accumulated. First LTR model
trained. nDCG@10 = 0.7891 on hold-out — +0.0263 over heuristic.

**Week 12**: 1487 accepted events. Second retrain. The model has
learned this team disproportionately accepts memories tagged
`security` over `performance` when the query is about request
latency (because their security team is the loudest reviewer in
PRs). nDCG@10 = 0.8042. Heuristic-only: 0.7628.

**Week 24**: 4218 events. Per-team model has converged.
hit@1 = 0.78 (heuristic was 0.61). Time-to-relevant-result halved.

The compounding is the win. Heuristic ranker is static; LTR drifts
toward what the team actually accepts.

### Scenario B: a query that nobody else asks

A junior dev queries "how do I deploy a Postgres migration without
downtime". The corpus has one canonical pattern but it's hidden in
the team's CANON because the title is "Use pg-migrate's
shadow-migration mode for zero-downtime schema changes" and the
junior dev doesn't know that vocabulary.

**Today** (heuristic): the canonical pattern is rank 4 because
"shadow-migration" doesn't appear in the query. The dev tries
something else and breaks production.

**With LTR** (after some training): the model has learned that
queries with "deploy" + "Postgres" + "without downtime" are usually
followed by acceptance of the shadow-migration pattern (other
seniors accept it; the model learns that signal). The pattern is
rank 1. The junior gets the right answer.

### Scenario C: drift detection

A team's ranker drifts toward over-promoting one author's memories
because that author writes high-quality, well-validated patterns.
Six months later, the author leaves; their patterns become stale
faster than others. The LTR model needs to *un-learn* the author
boost.

This works because LTR retrains nightly on the latest acceptance
data. As acceptance shifts, the model shifts. The half-life of bad
ranker bias is the retraining cadence.

A pure-heuristic ranker has no half-life — bad bias in
`INTENT_BOOSTS = ...` lives forever until a human retunes.

## 3.4 What the product becomes

**Today:** ranker is a fixed set of constants tuned by Memee's
maintainers. Customers get the same ranker.

**After:** ranker is a per-customer model that retrains nightly from
their team's acceptance data and improves silently.

That's the loop competitors don't have. Mem0, Zep, Letta — none log
per-search feature snapshots, so they have nothing to retrain
against. They tune ranker constants the same way Memee does today.

The competitive narrative shift:

| product | ranker quality drift | upgrade path |
|---|---|---|
| Mem0 / Zep / Letta | static (tuned by vendor) | new release |
| Memee today | static (tuned by vendor) | new release |
| Memee with #3 shipped | drifts toward team behaviour automatically | nightly retrain, no release needed |

## 3.5 Counterfactual / shadow ranker logging — the harder half

The plumbing-only fix isn't enough. We also need shadow ranker
logging to make the training data unbiased.

### How shadow logging works

Every production search runs the production ranker. **Additionally**,
every search runs an alternative ranker (e.g. the latest candidate
LTR model, or a fixed "control" ranker) and logs its top-30 to a
new column on `SearchEvent`:

```python
SearchEvent(
    id=...,
    query_text="how to deploy postgres migration",
    top_memory_id="prod-pattern-id",
    accepted_memory_id="prod-pattern-id",
    position_of_accepted=0,
    # NEW:
    shadow_ranker_version="ltr_v2_candidate",
    shadow_top_memory_id="other-pattern-id",
    shadow_position_in_prod=4,  # where the prod ranker had it
)
```

When acceptance lands, we know:
- Production ranker showed `prod-pattern-id` at rank 0; user accepted it.
- Shadow ranker would have shown `other-pattern-id` at rank 0.
- Did the user actually want `other-pattern-id`? **We don't know**
  because the user only saw production results.

But over many searches, we can do **off-policy evaluation**:
- For queries where the *shadow* ranker's top equals the user's
  acceptance, the shadow is at least as good.
- For queries where *production*'s top equals the acceptance and
  shadow's top would have been different, the shadow is potentially
  worse.
- Aggregate IPS (inverse propensity scoring) estimates: the shadow
  ranker would have hit@1 ≈ X with confidence interval Y.

### Cost

- One extra ranker call per search. If the shadow is the same
  model class as production (LTR vs LTR), this is ~5-10 ms
  additional. Acceptable.
- Two more columns on SearchEvent. Storage negligible.
- The math is standard (see Bottou et al. 2013 on counterfactual
  reasoning in production systems).

### Why this matters for Memee specifically

Memee's structural advantage is that we already log per-candidate
features at search time. Mem0/Zep/Letta don't, so they couldn't do
counterfactual logging even if they wanted — they'd have to recompute
features against current memory state, which is biased by edits
since the search.

## 3.6 Concrete improvements

| metric | today (heuristic) | LTR at convergence |
|---|---|---|
| ranker constants | 12 hand-tuned numbers | learned weights from telemetry |
| per-customer ranking | identical for everyone | tuned for team's acceptance pattern |
| improvement cadence | quarterly, by hand | nightly, automatic |
| typical hit@1 lift | — | +5-10 percentage points after 1k events |
| typical nDCG@10 lift | — | +0.02 to +0.04 after 1k events |
| time to first model | — | 6-12 weeks (depending on traffic) |
| time to convergence | — | 3-6 months |

The ranges are conservative and based on standard LTR literature
(LambdaMART papers, MS LETOR benchmarks). Production data may
differ.

## 3.7 What we'd lose

- **Cold start gating.** First 500 SearchEvents go to the
  heuristic. Users won't notice; teams that adopt Memee with low
  search volume will take longer to converge.
- **Operational complexity.** Canary routing + regression gate +
  retrain cron + model versioning. Each of these is shipping today
  on simple deployments and could break on multi-tenant ones.
  Mitigation: the canary auto-reverts on metric regression.
- **Model drift.** If a team's preferences change (new lead, new
  stack) the LTR model lags by one retrain cycle. Mitigation: a
  manual `memee ranker reset` returns to heuristic baseline.
- **Fairness questions.** If LTR learns "users on team X always
  click the second result" that's a self-fulfilling pattern.
  Mitigation: the canary keeps 90 % of traffic on heuristic so the
  LTR model can't drag the whole team into a rabbit hole. Plus
  diversity-preserving constraints in the ranker objective.
- **Telemetry burden.** Every search persists 25 snapshot rows
  (already shipped at 0.76 ms/search). Multi-million search/day
  customers will need the async telemetry queue (P1 perf, ready
  when needed).
- **Privacy.** Storing query text in SearchEvent is a data-
  retention question for regulated customers. Mitigation:
  per-org retention policy on `query_text` column;
  privacy-first embeddings (#6) ships PII redaction before save.

## 3.8 Why we haven't shipped yet

Gated on volume. Memee at launch is at <100 searches/week per org.
Realistic timeline:
- 8-12 weeks of organic customer traffic per org → first 500 events
- 1-2 weeks shipping shadow logging + retrain cron
- 2-4 weeks per-org model maturation

Total: 3-4 months from first paying customer to LTR operating
nightly per-team.

**Estimated effort once gate clears: 1-2 weeks** (counterfactual
logging columns + retrain cron + canary metrics + operator UX).

---

# ★★ 4. Neuro-symbolic review — tree-sitter AST + memory

## 4.1 What it does, mechanically

`engine/review.py` today extracts keywords from a diff with regex:

```python
detectors = [
    (r"\b(?:requests|httpx|aiohttp|session|client)\."
     r"(?:get|post|put|delete|patch|head|options)\b", "http"),
    (r"\beval\s*\(", "eval"),
    (r"\btimeout\b", "timeout"),
    (r"\b(?:password|api[_-]?key|secret[_-]?key|auth[_-]?token)"
     r"\s*=\s*[\"'][^\"']+[\"']", "secrets"),
    ...
]
```

Then it searches anti-patterns whose tags overlap with extracted
keywords and ranks by hybrid retrieval. That's the keyword-level
signal.

Neuro-symbolic review fuses that with **AST-level** signal. Concrete
pipeline:

1. Parse the diff hunks with tree-sitter (compiled language pack
   per language; language detection by file extension).
2. Walk the AST, extract structured triples:
   - `(call, requests.get, [{"name": "url", "value": "<var>"}])`
   - `(call, requests.get, [{"name": "url", "value": "<var>"},
                            {"name": "timeout", "value": 10}])`
3. Match each triple against `AntiPattern.trigger` and
   `AntiPattern.detection` columns, where the columns can now hold
   structured patterns:
   - `{"call": "requests.get", "missing_kwarg": "timeout"}`
   - `{"call": "exec", "args_count": ">=1"}`
   - `{"identifier_pattern": "(password|api_key|secret).*=.*['\"]+"}`
4. For each match, retrieve the linked memory + its evidence chain.
5. Emit a structured warning per match:
   ```json
   {
     "type": "anti_pattern",
     "memory_id": "ap-http-no-timeout",
     "title": "Always set HTTP timeout on outbound requests",
     "severity": "high",
     "trigger": "requests.get without timeout=",
     "consequence": "Hung worker thread; memory exhaustion under load",
     "alternative": "requests.get(url, timeout=10)",
     "match_location": {"file": "api.py", "line": 47, "kind": "ast"},
     "match_confidence": 0.94,
     "match_method": "ast"
   }
   ```

## 4.2 Three concrete scenarios

### Scenario A: PR adds a new HTTP client

```python
+def fetch():
+    return requests.get(url).json()
```

**Today** (regex review):

```
WARNING: HTTP usage detected (line 47)
  Memory: ap-http-no-timeout (Always set HTTP timeout)
  Confidence: 0.94
  Severity: high
  Trigger: requests.get without timeout
  → manually verify whether this code sets a timeout
```

The warning fires but it's vague — "HTTP usage detected." A human
has to manually check whether `timeout=` is missing. False
positives are common (some calls do set timeout via session
defaults).

**After** (AST review):

```
WARNING: HTTP timeout missing (line 47, AST analysis)
  Memory: ap-http-no-timeout
  Confidence: 0.94, Match confidence: 0.99
  AST evidence:
    Call: requests.get(url) at api.py:47
    Args: [url]
    Kwargs: {}      ← no timeout= kwarg
  Memory says:
    "Unbounded requests.get hangs the worker thread forever."
    Evidence chain:
      2025-09-22 prod-api PR-3187: incident-2025-09-22 hung-worker
                                    issue link, root-caused timeout absence
  Suggested fix:
    requests.get(url, timeout=10)
  → confidence 99%; auto-fix available
```

The AST sees the call signature precisely. False positive rate
drops to near zero. The suggested fix is structured (you can
auto-apply it).

### Scenario B: PR adds eval-like dynamic dispatch

```python
+# Run user-provided expression
+result = eval(user_input, {"__builtins__": {}}, {})
```

**Today** (regex):

```
WARNING: eval() usage (line 102)
  Memory: ap-eval-rce (eval on user input)
  Trigger: eval()
  → review for security
```

The regex matches both safe and unsafe `eval` patterns. It can't
tell the difference between this (PROBABLY UNSAFE despite the
sandbox attempt) and a benign use like `eval("1 + 2")`.

**After** (AST):

```
SECURITY WARNING (line 102, AST analysis)
  Memory: ap-eval-rce (eval on user input)
  Confidence: 0.97, Match confidence: 0.95
  AST evidence:
    Call: eval(user_input, {"__builtins__": {}}, {})
    Arg 0: identifier 'user_input'  ← likely user-controlled
    Arg 1: dict literal {"__builtins__": {}}
    Arg 2: dict literal {}
  Sandbox detection:
    Restricted globals: {"__builtins__": {}}
    BUT: bypassable via __class__.__mro__ chain (CVE-class)
  Memory says:
    "Even sandboxed eval is bypassable. Parse explicitly with
     ast.literal_eval if needed."
    Evidence chain:
      2024-11-13 security audit: same-class CVE in old code
  → confidence 97%; auto-suggestion: ast.literal_eval(user_input)
```

The AST sees both the call and its argument structure. It can
recognise the sandbox attempt and flag the known bypass.

### Scenario C: PR adds a classic SQL injection

```python
+query = f"SELECT * FROM users WHERE id = {user_id}"
+cursor.execute(query)
```

**Today** (regex): no warning. The regex for `\.execute\(` matches,
but it can't tell that the argument is an f-string with an
embedded variable.

**After** (AST):

```
SECURITY WARNING (line 33-34, AST analysis)
  Memory: ap-sql-injection (parameterize, never interpolate)
  Confidence: 0.99, Match confidence: 0.99
  AST evidence:
    Assignment: query = <f-string>
    F-string parts: ["SELECT * FROM users WHERE id = ", user_id]
    Followed by: cursor.execute(query)
    → user-controlled value in SQL string
  Memory says:
    "f-string SQL is the canonical injection vector. Use
     parameterised queries: cursor.execute('SELECT ... = %s',
     (user_id,))"
  → confidence 99%; auto-suggestion provided
```

The AST sees the f-string composition and the subsequent execute
call. Regex couldn't.

## 4.3 What the product becomes

**Today:** review.py is the smallest module of Memee — a regex +
search wrapper. Most teams use a real linter for AST-level checks
and treat Memee's review as a "nice to have" cross-project anti-
pattern surface.

**After:** review.py becomes a **first-class code analysis surface**
that sits next to ruff/mypy/sonarqube but is uniquely informed by
the *organizational memory* of validated patterns. It's the security
+ quality gate that learns from your team's actual review history.

This is a competitive shift. Today's competition:

| product | what it does | gap |
|---|---|---|
| ruff | fast Python linter | static rules; no team-specific context |
| mypy / pyright | type checker | type-only; no quality patterns |
| bandit | Python security scanner | rule-based; no learning |
| CodeRabbit, Greptile | AI code review | LLM-call per PR; expensive; no persistent memory |
| Sourcegraph Cody | code intelligence | no patterns DB; no review feedback loop |
| **Memee + #4** | AST + persistent memory + agent feedback loop | unique combination |

The unique combination is the unfair advantage. Tree-sitter and AST
matching is generic; the *team's validated anti-patterns* is the
proprietary substrate. Nobody else has both.

## 4.4 Measurable headroom — measured

The 207-query harness has a `diff_review` cluster (n=30) that's the
**weakest** of all seven clusters:

| cluster | n | BM25-only | + cross-encoder | + AST review (estimate) |
|---|---:|---:|---:|---:|
| diff_review | 30 | **0.5557** | 0.6192 | **0.85+** |
| anti_pattern_intent | 32 | 0.8266 | 0.8194 | (already strong) |
| code_specific | 42 | 0.8355 | 0.8497 | (already strong) |

The 0.5557 baseline is honest data — pure-keyword review can't
distinguish `requests.get(url)` from `requests.get(url, timeout=10)`
because both contain the keyword `requests.get`. AST does. The
cross-encoder lifts to 0.6192 because some diffs include the keyword
`timeout` either in a comment or another line. With AST reading the
actual call args, the precision approaches the limit of what the
underlying anti-pattern memory can answer.

The 0.85+ estimate: AST review on a properly-structured anti-
pattern dataset (where `AntiPattern.detection` has concrete AST
patterns) can hit precision of 0.95+ on individual rules. Across a
30-query test set, macro nDCG@10 of 0.85 is the floor; 0.90 is
plausible.

That's a +25 to +30 nDCG-point lift on the cluster — by far the
largest single gain available.

## 4.5 What we'd lose

- **`tree-sitter` as a new optional dep**, plus language packs for
  every language. Each pack ~2-5 MB. Multi-language wheel grows
  significantly. Mitigation: ship a `[review-python]` extra (just
  Python pack) as the lightweight default; `[review-all]` for the
  full set.
- **Maintenance surface.** Tree-sitter language grammars evolve;
  each major language version may need a re-pin. Estimate: 1-2
  hours/quarter of grammar maintenance.
- **Per-language coverage gap.** First version covers Python, JS,
  TS. Niche languages (Erlang, Crystal, F#) won't have AST review.
  Mitigation: graceful degrade to keyword review when the language
  isn't supported. The keyword path stays as the safety net.
- **AST pattern authoring burden.** New `AntiPattern.detection`
  column needs structured patterns ("call: requests.get,
  missing_kwarg: timeout") instead of regex. Old anti-patterns
  remain regex-compatible; new ones get the option to write AST
  patterns. Migration is incremental.
- **False negatives at AST boundary.** AST won't catch e.g.
  `getattr(requests, "get")(url)` — dynamic dispatch is invisible
  to static parse. Mitigation: keyword review remains as the
  fallback layer; the two paths are additive, not exclusive.

## 4.6 Per-language coverage roadmap

| phase | languages | wheel size impact | ship target |
|---|---|---|---|
| v1 | Python | +5 MB | Q3 |
| v2 | + JavaScript, TypeScript | +12 MB | Q4 |
| v3 | + Go, Rust | +15 MB | Q1 next year |
| v4 | + Swift, Kotlin | +12 MB | Q2 next year |

Shipping order is by user demand. Python first because Memee's
own users are Python-heavy. JS/TS next for frontend-heavy teams.

## 4.7 Integration with existing scanners

Memee with #4 doesn't *replace* ruff/mypy/bandit — it integrates
with them. Concrete:

- **Pre-commit hook**: `memee review --diff $STAGED` runs after
  ruff and bandit. Memee's warnings reference team-specific
  anti-patterns; the linter warnings are generic.
- **PR comment bot**: posts Memee warnings inline on the PR
  alongside (or below) ruff/bandit output.
- **CI failure modes**: configurable via `.memee.yml`:
  - `block_on_critical: true` — fail CI on any critical anti-pattern
    match.
  - `block_on_high: false` — high-severity warnings are advisory.

The narrative: "ruff catches generic mistakes; Memee catches the
mistakes your team has already learned to avoid."

## 4.8 What changes in how users feel about Memee

Today review.py is a "nice to have" — most teams use Memee for
search and run their own linter for the AST-level stuff.

After: review.py is the **primary** review surface for teams using
agents. The AST + memory combination is what the team's agent
*should have known* without being told. That's a different product
positioning.

For agents specifically: a Claude/GPT/Gemini agent can call `memee
review` on its own diffs before submitting. The AST signal is
high-precision — the agent can act on it (`auto-fix: add timeout=10`)
without human review for the high-confidence cases.

That's automation. That's the product narrative shift.

## 4.9 Why we haven't shipped yet

review.py was the lowest-priority module before R14 measured
diff_review as the worst cluster on the 207q harness. The
measurement made it the obvious next target.

**Estimated effort: 2-3 weeks** for a Python+JS+TS v1 (tree-sitter
integration, AST traversal patterns for ~20 common anti-patterns,
per-language test corpus). Subsequent languages: 3-4 days each.

---

# ★ 5. Expected-value router — token-cost-aware briefing

## 5.1 What it does, mechanically

Today `briefing()` returns top-N memories by RRF score under a
static token budget. It ranks by *predicted relevance*, not by
*expected value*.

EV-routing changes the rank function:

```
EV(memory | query, budget) = P(relevance | query) × impact(memory)
                              − tokens(memory) × token_price
```

Each term is something Memee already tracks:

- **`P(relevance | query)`**: the calibrated cross-encoder score (R12
  P1 isotonic curves applied to the rerank score).
- **`impact(memory)`**:
  ```
  impact(m) = w1 × log(1 + application_count)
           + w2 × log(1 + mistakes_avoided)
           + w3 × validation_count_norm
           + w4 × project_count_norm
  ```
  where weights are calibrated against measured outcomes from
  `outcome_evidence_type` populated rows.
- **`tokens(memory)`**: title + content character count / 4 (the
  router already uses this approximation).
- **`token_price`**: configurable per model in `.memee.yml`:
  ```yaml
  models:
    sonnet-4:
      input_per_1k: 0.003
      output_per_1k: 0.015
  ```

The briefing then includes a memory iff `EV > 0` — i.e. the
expected reward of including it exceeds its token cost.

## 5.2 Three concrete scenarios

### Scenario A: production debugging task

Query: `"fix prod hang in payment service"`

Memories that score well on relevance:

| memory | P(rel) | impact | tokens | price | EV |
|---|---:|---:|---:|---:|---:|
| ap-http-no-timeout (CRITICAL) | 0.94 | 8.2 | 320 | $0.001 | $7.71 |
| pat-circuit-breaker | 0.81 | 5.4 | 480 | $0.001 | $4.37 |
| pat-retry-with-backoff | 0.72 | 4.1 | 420 | $0.001 | $2.95 |
| obs-payment-service-version | 0.68 | 1.2 | 180 | $0.001 | $0.81 |
| dec-use-postgres-not-mysql | 0.43 | 6.8 | 220 | $0.001 | $2.92 |

Briefing includes all 5 memories, total cost $0.005. Total expected
value $18.76 (highly worth the cost).

**Today's router** would have included the same set by relevance
ranking, but if budget were tight (`token_budget=400`), it would
have truncated by rank — possibly cutting `pat-circuit-breaker`
which is the second-highest EV. After: budget enforces by EV
cumulative, not rank.

### Scenario B: low-budget mode

Query: `"add a button to the React app"`

| memory | P(rel) | impact | tokens | price | EV |
|---|---:|---:|---:|---:|---:|
| pat-react-tailwind | 0.85 | 3.2 | 350 | $0.001 | $2.37 |
| pat-react-component-patterns | 0.78 | 4.5 | 480 | $0.001 | $3.03 |
| pat-react-accessibility | 0.65 | 2.1 | 290 | $0.001 | $1.07 |
| ap-react-inline-styles | 0.58 | 2.8 | 220 | $0.001 | $1.40 |

User is on a Haiku model (fast, cheap): `token_price = $0.0005/k`.

Same memories, scaled cost. Plus user has set
`brief_min_ev_per_token: 0.005`.

After EV filter: only memories with EV/token > 0.005 are included.
That's the top 2: `pat-react-tailwind` (EV/token = $0.0068) and
`pat-react-component-patterns` (EV/token = $0.0063).

Briefing sent to the agent: 2 memories, ~830 tokens, cost
$0.000415, expected return ~$5.40. The agent's task gets the
critical context without the marginal noise.

### Scenario C: critical-only mode

```bash
$ memee brief --task "fix payment outage" --mode critical-only
```

EV filter at extreme: only show memories where `EV > $1.00`. For a
critical incident, you want only the high-impact critical anti-
patterns; everything else is noise.

Result: 1 memory shown — `ap-http-no-timeout` (CRITICAL, EV=$7.71).
The agent gets the one rule that matters and acts on it.

This is a new product mode — the same router, different EV filter.

## 5.3 What the product becomes

**Today narrative:** "Memee saves tokens by routing only relevant
memory."

**After narrative:** "Memee maximizes expected value under your
token budget. It only shows you a memory if the math says including
it pays for itself."

That's a different sales conversation:

| today's customer Q | answer | tomorrow's customer Q | answer |
|---|---|---|---|
| "how much does Memee cost?" | "$49/mo for Team" | "what's my token bill with Memee?" | "depends on your model + EV threshold; usually 30-60% reduction" |
| "what does Memee do?" | "memory for AI agents" | "what's my $/incident with vs without Memee?" | "$X without, $Y with; ROI shown on dashboard" |
| "why pick Memee over Mem0?" | "more features" | "why pick Memee?" | "the only tool that ships an EV-budgeted router; rest just rank" |

The pricing conversation also shifts. Today it's "subscription tier."
After: customers can configure `token_price` per model and Memee
auto-tunes to their cost stack.

## 5.4 Concrete improvements

| use case | today | after |
|---|---|---|
| critical anti-pattern about RCE | always shown | shown if EV > cost (almost always; high impact) |
| medium-impact pattern about caching | shown if it ranks | shown if EV > cost; lower-impact pattern with same rank but high token cost gets dropped |
| low-impact observation tagged for the project | shown if it ranks | rarely shown (impact dominates) |
| custom-budget mode (`memee brief --budget 200`) | hard token cap | soft EV-cap; shows only memories where EV per-token > $0.01 |
| critical-only mode | not exposed | new flag `--mode critical-only` shows only EV > $1.00 |

The "only show me what's worth showing" mode is the *new* product
offering. Today the router has a static budget. After, it has a
budget *and* a quality gate.

## 5.5 What we'd lose

- **Calibration prerequisite.** EV math collapses when P(relevance)
  is mis-calibrated. R12 P1 shipped the calibration substrate but
  it requires production telemetry to fit useful curves. So this
  is gated behind LTR + calibration data fill — same gate as #3.
- **Impact telemetry burden.** `outcome_evidence_type` needs to be
  populated on real ProjectMemory rows. Today it's nullable and
  rarely set. The EV math is only as good as the impact signal.
  Mitigation: the calibration falls back to `application_count` as
  an impact proxy when explicit outcome data is sparse.
- **Determinism trade.** Today briefing output is determined by
  ranker score + budget. After, output depends on the calibrated
  probability — which can shift over time as the curves retrain.
  Two days apart, the same query may produce different briefings.
  Mitigation: pin the calibration version per-org and rev it
  explicitly.
- **Configuration complexity.** Token prices, EV thresholds,
  budget modes — three more dials. Mitigation: sensible defaults
  per model; advanced users tune via `.memee.yml`.

## 5.6 The dashboard "EV math" view

Today the dashboard shows recent memories. After, a new panel:

```
Briefing for "fix payment outage" (today, 09:32)
  Total cost:  $0.005     Total EV:  $18.76    ROI: 3,752×
  Memories shown: 5 of 8 candidates

  Included:
    [CRIT] ap-http-no-timeout       P=0.94  Imp=8.2  EV=$7.71
    [CAN]  pat-circuit-breaker      P=0.81  Imp=5.4  EV=$4.37
    ...

  Excluded (EV < threshold):
    [OBS]  obs-payment-service-version  P=0.68  Imp=1.2  EV=$0.81
       (excluded: EV/token = $0.0045 below 0.005 threshold)
```

The user sees the math, can audit the decisions, can tune the
threshold. That's a different UX from "trust the ranker."

## 5.7 Why we haven't shipped yet

Same gate as LTR — needs production telemetry to populate the
`outcome_evidence_type` field at scale. Once that's flowing, this
is 1-2 weeks of work.

**Estimated effort once gate clears: 1-2 weeks** (EV calculation
+ config schema + dashboard view + CLI flags).

---

# ★ 6. Privacy-first embeddings + per-scope encryption

## 6.1 What it does, mechanically

Memee already does some privacy work right: embeddings use the
local `all-MiniLM-L6-v2` model, so PII never leaves the install.
But three pieces are missing:

1. **PII / secret redaction before record.** Today `memee record`
   accepts arbitrary content. If an agent records:
   > "the customer's email is alice@example.com and her API key is
   > sk-abc123"
   that string sits unredacted in `memories.content` and goes into
   the embedding. The embedding model can't be reverse-engineered
   to recover the email, but the SQLite row can be read directly.

2. **Per-scope encryption at rest.** Today the SQLite file is
   plain. An attacker with read access to `~/.memee/memee.db`
   gets the entire memory store. There's no per-org encryption
   key, no scope-aware encryption.

3. **Retention policy on `evidence_chain`.** Once a memory is
   recorded, its evidence chain (PR URLs, commit hashes, agent
   feedback strings) is retained forever. Some of those reference
   private business decisions that shouldn't outlive the project
   that generated them.

## 6.2 The redaction layer

Pre-record scanner with 4 rule families:

```python
REDACTION_RULES = [
    # Email addresses
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[REDACTED_EMAIL]"),

    # API keys / secrets (heuristic: long base64-ish strings)
    (r"\b(sk|pk|ghp|github_pat)[-_][A-Za-z0-9]{20,}\b", "[REDACTED_SECRET]"),

    # AWS access keys
    (r"\bAKIA[0-9A-Z]{16}\b", "[REDACTED_AWS_KEY]"),

    # Phone numbers (US + EU formats)
    (r"\b(?:\+?1[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b", "[REDACTED_PHONE]"),

    # Credit cards (Luhn-validated)
    (r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b", "[REDACTED_CARD]"),

    # Bearer tokens, JWT
    (r"\b(?:Bearer\s+)?[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b",
     "[REDACTED_JWT]"),

    # SSH keys
    (r"-----BEGIN [A-Z ]+PRIVATE KEY-----.*?-----END [A-Z ]+PRIVATE KEY-----",
     "[REDACTED_SSH_KEY]"),

    # Internal hostnames (configurable per-org)
    # ... org-specific rules
]
```

Per-scope policy:

| scope | redaction strictness |
|---|---|
| personal | minimal (only obvious secrets) |
| team | strict (all of the above + custom org rules) |
| org | strictest (refuse to record if redaction triggers; force human review) |

Behaviour:
- `memee record` runs redaction → if any rule fires, choose:
  - `personal`: silently redact, log warning
  - `team`: redact, attach `redacted_count` to the memory
  - `org`: refuse, return error with rule that fired

## 6.3 Per-scope encryption at rest

Use **SQLCipher** (the SQLite encryption extension; well-vetted,
production-grade since 2009).

Setup:

```bash
$ pip install memee[encryption]
$ memee encryption init
Generating master key: ~/.memee/keys/master.key (chmod 600)
Generating org key: ~/.memee/keys/orgs/default.key (chmod 600)
Migrating ~/.memee/memee.db → encrypted format... (4.2 MB → 4.3 MB)
Done. New ENV: MEMEE_ENCRYPTION_KEY_PATH=~/.memee/keys/orgs/default.key
```

Per-org isolation:
- Master key in `~/.memee/keys/master.key`.
- Per-org keys derived via PBKDF2 with org-id as salt.
- SQLCipher uses AES-256-CBC with HMAC-SHA512 (default since v4).
- `MEMEE_ENCRYPTION_KEY_PATH` selects which org's key to use at
  startup.

memee-team adds:
- HSM-backed master key (AWS KMS, GCP KMS, Azure Key Vault).
- Key rotation via `memee encryption rotate` (re-encrypts the DB
  with a new key; old key stays available for backups).
- Hardware-bound keys for federal deployments (Touch ID / Windows
  Hello / FIDO2).

## 6.4 Retention policy

New columns on `Memory` and `ProjectMemory`:

```python
class Memory(Base):
    ...
    retention_until = Column(DateTime, nullable=True)   # auto-redact after this
    retention_policy = Column(String(50), default="default")
                                       # "default", "compliance", "ephemeral"
```

Scheduled job (runs weekly via dream cycle):

```python
def _enforce_retention(session):
    expired = session.query(Memory).filter(
        Memory.retention_until < utcnow(),
        Memory.maturity != MaturityLevel.DEPRECATED.value,
    ).all()
    for m in expired:
        # Hash + redact: keep the row for graph integrity, but blank
        # content + evidence chain
        m.content = f"[RETAINED_HASH:{hashlib.sha256(m.content.encode()).hexdigest()[:16]}]"
        m.evidence_chain = []
        m.maturity = MaturityLevel.DEPRECATED.value
        m.deprecated_reason = f"Retention policy {m.retention_policy} expired"
```

Per-scope defaults:

| scope | default retention |
|---|---|
| personal | infinite |
| team | 2 years (compliance default) |
| org | 7 years (audit default) or per-policy |

GDPR right-to-erasure: `memee privacy erase --user alice@example.com`
walks every memory referencing that email, redacts content + evidence
chain, retains the row for graph integrity.

## 6.5 Three concrete scenarios

### Scenario A: HIPAA-covered healthcare org

A clinical-decision-support team using Memee for AI agent rules.
HIPAA requires:
- Access controls (who can read what).
- Audit trail (who accessed what, when).
- Encryption at rest (AES-256 minimum).
- Right to delete on patient request.

**Today** (Memee as shipped): nope on all four. Memee is HIPAA-
ineligible. The org cannot legally deploy it on PHI-adjacent
workloads.

**After** (#6 shipped):
- Access controls via `memee-team` scope (already exists; extended
  to enforce on read).
- Audit trail via SearchEvent + new `MemoryAccessLog` table.
- Encryption at rest via SQLCipher.
- Right to erasure via `memee privacy erase`.

The org signs a DPA, deploys Memee, opens HIPAA Day One conversation
with auditors.

### Scenario B: GDPR-regulated EU SaaS

European customer's EU-hosted SaaS, has Memee in their stack.
Their user requests data deletion under GDPR Article 17.

**Today**: data deletion requires the SaaS engineer to grep through
SQLite for any record referencing the user, manually delete each
one, hope nothing is missed in evidence_chain.

**After**:
```bash
$ memee privacy erase --user-pattern "alice@example.com" --scope org
Found 3 memories referencing the pattern.
Walking evidence chains...
Found 12 entries with the pattern in evidence_chain.
Found 7 ProjectMemory rows with the pattern in outcome_evidence_ref.
Total redactions: 22.
Will mark 3 memories as deprecated with retention=immediate.
Confirm? [y/N]: y
Done. Audit log entry: ~/.memee/audit/2026-04-25-erase-alice.log
```

The audit log is the GDPR compliance evidence.

### Scenario C: financial-services SOX-regulated

Quarterly SOX audit asks: "show me every change to controls over
revenue recognition, with timestamps and approver."

**Today**: nope. The data is in Memee but the audit format isn't
exposed. SOX-eligible? No.

**After**: the canon ledger (#2) provides the audit trail; #6
provides the access controls and retention. Combined, they're a
SOX-eligible substrate.

## 6.6 What the product becomes

**Today:** Memee is an open-source memory tool with API integrations.
Regulated industries can't deploy it.

**After:** Memee is enterprise-ready. SOC2 Type II audit becomes
possible. HIPAA, GDPR, SOX-regulated industries can deploy.

That's the gate to a buyer category we currently can't serve.

## 6.7 Concrete improvements

| dimension | today | after |
|---|---|---|
| who can deploy | startup eng teams | + healthcare, finance, legal, government |
| DPA conversation | "we'll get back to you" | "here it is, signed by counsel" |
| audit readiness | none | SOC2 Type II in scope |
| key management | filesystem | per-org encryption, HSM in memee-team |
| retention | infinite | configurable per-scope |
| user-visible delete | "memee deprecate" | also wipes evidence chain |
| GDPR Article 17 | manual | `memee privacy erase` |
| HIPAA covered entity eligible | no | yes |
| SOX control evidence | manual | canon ledger + audit log |

The first row alone is the gamechanger. Every other row is the
work to make that row honest.

## 6.8 What we'd lose

- **Complexity.** Encryption-at-rest is hard to do safely. Key
  rotation, recovery, backup all become first-class concerns.
  Mitigation: lean on SQLCipher (well-trodden path; just optional
  dep `[encryption]`).
- **Performance overhead.** ~5-10 % search latency from encryption
  transparently applied. Acceptable.
- **Indexing constraint.** Encrypted FTS5 doesn't work well —
  queries leak token-level information through access patterns.
  Mitigation: keep the redaction layer doing the heavy lifting
  before content enters FTS, so the encrypted columns are only the
  ones that don't need search (`evidence_chain`, source URLs,
  `outcome_evidence_ref`).
- **Backup complexity.** Encrypted backups require the org key;
  losing the key loses the data. Mitigation: HSM-backed master key
  for `memee-team` deployments; recovery seed phrase for OSS.
- **Compliance audit cost.** SOC2 Type II is ~$50-100k for the
  first audit. Mitigation: that cost is the customer's, not
  Memee's. We just enable it.
- **No nDCG win.** Like the evidence graph, this isn't a ranker
  upgrade. It's an enabler for a buyer category.

## 6.9 Why we haven't shipped yet

No regulated customer signed yet. We're inverted on the build/ask
axis: this is the *enabler* for that conversation, but it takes
2-3 weeks to build. Reasonable people disagree on whether to build
it speculatively or wait for a signed prospect.

**Estimated effort: 2-3 weeks** (PII scanner + SQLCipher
integration + retention policy + audit log + privacy CLI).

---

# Comparison summary — what each gamechanger changes

The honest meta-table:

| # | gamechanger | nDCG impact | latency impact | new buyer | effort |
|---|---|---|---|---|---|
| 1 | Cross-encoder default-on | +0.0355 macro (measured) | +40 ms p50 | none (UX win) | 1 wk |
| 2 | Evidence graph as ledger | +0.01-0.02 (estimate) | none | compliance / audit / legal | 3-4 wk |
| 3 | LTR + counterfactual | +0.02-0.04 at convergence | +5-10 ms (canary) | none (operational win) | 1-2 wk |
| 4 | Neuro-symbolic review | +0.25-0.30 on diff_review | +50-100 ms on review | security / quality | 2-3 wk |
| 5 | EV router | indirect (via budget) | none | budget-conscious | 1-2 wk |
| 6 | Privacy-first | none | -5-10% search | regulated industries | 2-3 wk |

Each one is a different product axis. Stacking them doesn't
multiplicatively compound (the markets are different) — but each
opens a category we currently can't serve.

# Sequencing — what to do first

If we had four weeks, here's the order I'd ship:

### Week 1 — ★★★ Cross-encoder default-on (#1)

Lowest-risk gamechanger. Code is in the repo. Measurement has
p=0.0002. Decisions: bundle vs lazy-download model; default-on for
OSS or only memee-team. One week of packaging work + one round of
UX testing.

Outcome: README narrative changes from "if you opt in, …" to "out
of the box, …". Most felt by new users.

### Weeks 2-3 — ★★ Neuro-symbolic review (#4)

Highest measured ROI. The diff_review cluster is the worst on the
207q harness; AST-aware review is what closes it. Tree-sitter is a
known quantity. The review.py module is small enough that this is
two weeks of focused work.

Outcome: review.py becomes a sellable surface, not a side feature.

### Week 4 — ★★★ Evidence graph as canon ledger (#2)

Surface the R9 plumbing. CLI surface (`memee why`, `memee
canon-state`) is a few hundred lines on top of existing schema.
Dashboard graph view as v0.5 with basic node-link diagram.

Outcome: enterprise-ish narrative becomes credible.

### Quarter 2 — ★★ LTR (#3) and ★ Expected-value router (#5)

Both gated on telemetry volume. Once we have 500+ accepted
SearchEvents, LTR retrains nightly and the EV router uses the
calibrated probabilities. These compound: LTR makes the ranker
better, the router makes the ranking visible to users as a $$
calculation.

### Quarter 3 — ★ Privacy-first embeddings (#6)

Build on first regulated prospect. Don't speculate.

---

# What this analysis is honest about

- **The numbers cited for #1 are measured.** 0.7273 → 0.7628 is
  `tests/retrieval_eval.py --save r14_rerank_on` against current
  main. Reproducible.
- **The cluster baselines for #4 are measured.** diff_review =
  0.5557 BM25-only is from the same harness.
- **All other numbers are estimates.** The "diff_review goes from
  0.62 to 0.85+ with AST" is my prediction, not a measurement —
  AST review hasn't been built yet.
- **All of these are gated.** Even #1 (lowest-risk) is gated on a
  packaging decision. None ship next week without a real product
  call.
- **At least one will be wrong.** When you list six gamechangers,
  one turns out smaller than predicted. The whole point of
  measuring before shipping (R14 #1's honest-negative result on
  field-aware BM25) is that we'll know which one within a quarter.

The five-year version of this list will be different. The two-month
version is: ship #1, then #4, then surface #2.
