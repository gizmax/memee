# Memee

[![tests](https://img.shields.io/badge/tests-passing-brightgreen)](#tests)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![pypi](https://img.shields.io/badge/pypi-memee-orange)](https://pypi.org/project/memee/)

![Memee — You shouldn't have to teach it twice.](https://memee.eu/og-image.png)

**You shouldn't have to teach it twice.**

Every chat is a new intern. You teach them Monday. By Friday they've quit. Memee writes it on the wall. The next intern reads it. So does your teammate's. So does the next model.

Memee shares experience across projects, across agents, across models, and across the people on your team.

```bash
pipx install memee         # recommended for a CLI tool
# already installed? pipx upgrade memee
```

> **For teams and companies.** This OSS release is single-user and self-hosted. If you want the same memory shared across a whole team — with cross-developer, cross-agent, cross-project, cross-model canon building into **company-wide institutional knowledge** — there's a paid Team edition at [memee.eu](https://memee.eu). Same engine, plus SSO, audit log, and shared scope. Flat **$49 / month** for up to fifteen seats, or **$12k / year** Enterprise with SOC 2 and air-gap.

---

## What Memee actually does

Three jobs. Executed relentlessly.

### Records.
Every pattern, every decision, every near-miss. One turn at a time, across every agent on every project.

> *7-task A/B: time −71 %, iterations −65 %, mistakes 0.*

### Routes.
Not a dump. A briefing. At task start, the router picks the 5–7 memories the agent actually needs — inside a hard 500-token budget. Your `CLAUDE.md` grows forever. Memee doesn't.

> *Measured ~40 tokens per task against a ~2,160-token median baseline.*

### Scores.
A lesson earns trust by surviving. A second model family agrees: confidence ×1.3. A second project re-uses it: ×1.5. Earn both and it climbs the ladder — hypothesis, tested, validated, canon.

> *One canon. Four model families. Seventeen engines.*

---

## Install and first use — sixty seconds

```bash
pipx install memee
memee setup                       # writes MCP + hooks to ~/.claude/settings.json
memee pack install python-web     # day-one canon, 30 entries

# Record something you just learned.
memee record pattern "retry with jitter" \
  --tags reliability,http \
  -c "Exponential backoff, capped at 30s, idempotent verbs only."

# Ask the canon what it knows about a snippet.
memee why "eval(user_input)"

# Health check (rerank state, hooks, cache).
memee doctor
```

That's it. Memory lives in `~/.memee/memee.db`. No account. Core read/write is fully local. Vector embeddings are optional — on by default via `sentence-transformers`, which fetches a ~80 MB model on first use. Set `TRANSFORMERS_OFFLINE=1` to skip.

After `memee setup` your agent doesn't have to remember Memee exists. SessionStart and UserPromptSubmit hooks land a routed briefing into Claude Code's context automatically; Stop fires a post-task review. Opt out with `memee setup --no-hooks` if you want the plain MCP-only mode.

---

## The architecture, on one page

Small engines on SQLite + FTS5 + a 384-dim embedding space.

| Layer | Job |
|---|---|
| **Router** | Task-aware briefing. Budget-capped. |
| **Quality gate** | Validates, deduplicates, rates every incoming memory before it earns a row. |
| **Confidence scoring** | Adaptive. Cross-project ×1.5. Cross-model ×1.3. Both stacked ×1.95. |
| **Lifecycle** | hypothesis → tested → validated → canon → deprecated. Old advice ages out. Good advice gets promoted. |
| **Dream mode** | Nightly. Connects related memories, surfaces contradictions, elevates canon. |
| **Propagation** | A validated pattern auto-pushes to projects with matching stack or tags. Fix once. Benefit everywhere. |
| **Review** | `git diff \| memee review -` scans a changeset against known anti-patterns. Institutional memory enters code review. |
| **CMAM bridge** | Push canon to Anthropic's Managed Agents Memory at `/mnt/memory/`. Claude sees canon on turn one — no MCP round-trip. |

Deeper notes: [CLAUDE.md](CLAUDE.md). CMAM spec: [docs/cmam.md](docs/cmam.md). Review engine: [docs/review-fixes.md](docs/review-fixes.md).

---

## The token math

> Numbers below are **internal simulations and measured benchmarks**, not independent third-party evaluations. Treat them as suggestive, not conclusive.

The thing Memee saves isn't the first page. It's the slope.

- **Without Memee, median:** ~2,160 tokens per turn. That's a `CLAUDE.md` / `AGENTS.md` across 27 popular OSS repos (langchain, vercel/ai, prisma, zed, openai/codex, and others), sampled via `gh api`. Claude Code and Cursor load it in full on every session.
- **Without Memee, grown teams:** 6k–15k. p95 of the sample hits 9,600. One published outlier reached 42,000.
- **With Memee:** 500-token cap, measured average ~40 tokens per briefing (min 18, max 67 across 10 task queries on a 500-pattern corpus).
- **So the saving, honestly:** ≥77 % at median. ≥95 % at 10k-grown teams. ≥99 % at the 42k outlier. And unlike `CLAUDE.md`, it's bounded. Your library grows. Per-turn context doesn't.

Reproduce locally:

```bash
memee benchmark          # OrgMemEval v1.0
pytest tests/ -v         # full suite
```

Full methodology + per-repo file sizes: [docs/benchmarks.md](docs/benchmarks.md).

---

## Benchmarks

- **OrgMemEval v1.0**: 92.3 % across propagation, avoidance, maturity, onboarding, recovery, calibration, synthesis. Competitors on the same scenarios: MemPalace 0.9, Letta 1.3, Zep 2.3, Mem0 3.5 (the closest). v2.0.0 retired the autoresearch scenario alongside the engine.
- **7-task A/B (with / without Memee):** time −71 %, iterations −65 %, quality 56 % → 93 %, ROI ≈ 10.7× at the $49 / month Team tier.
- **GigaCorp simulation**, 100 projects, 100 agents, 18 months: incidents 12/mo → 3/mo, annual ROI ≈ 3× at the same flat Team tier.
- **Retrieval**: 207-query × 255-memory eval harness with 7 difficulty
  clusters. BM25-only baseline `nDCG@10 = 0.7273`. With the optional
  cross-encoder rerank (`MEMEE_RERANK_MODEL=cross-encoder/ms-marco-
  MiniLM-L-6-v2`, `pip install memee[rerank]`): `nDCG@10 = 0.7628`
  (+0.0355, p=0.0002). Run `python -m tests.retrieval_eval` to
  reproduce.

---

## Using it with Claude, GPT, Gemini

An MCP server with 24 tools ships with the install. Drop this into `~/.claude/settings.json` — or the Cursor / Continue / any MCP-capable client equivalent:

```json
{
  "mcpServers": {
    "memee": { "command": "memee", "args": ["serve"] }
  }
}
```

Memee auto-detects the caller's model family from `MEMEE_MODEL`, `ANTHROPIC_MODEL`, or `OPENAI_MODEL` and tags every write with `source_model`. That's how confidence scoring knows when Claude and Gemini agree — and when they don't.

Quick CLI tour:

```bash
memee brief --task "write unit tests"   # PUSH: routed briefing
memee check "about to add eval() here"  # PULL: anti-pattern check
memee propagate                         # cross-project diffusion
memee dream                             # nightly: connect, contradict, promote
memee cmam sync                         # push canon to /mnt/memory/ for Claude
```

---

## Pricing

Flat per team. Same engine in every tier.

| | Free | Team | Enterprise |
|---|---|---|---|
| | **$0** forever · MIT | **$49 / month flat** — up to 15 seats, annual | **from $12k / year** — unlimited seats |
| For | Solo developers. Self-hosted. Full engine, local scope. | Teams that want shared memory, SSO, and an audit trail. | Regulated industries, air-gap, SOC 2. |
| Stack | Router, quality gate, dream mode, CMAM sync, all 4 model families | Everything in Free + team/org scope with promotion workflows, SSO (SAML / OIDC), RBAC, audit log export, Postgres / Turso backend, multi-agent dashboard, 24h SLA | Everything in Team + SOC 2 Type II, DPA, SCIM, on-prem license key, dedicated CSM, 4h SLA, custom MCP integrations |

Between fifteen and a hundred seats, and no SOC 2 needed? Email [info@memee.eu](mailto:info@memee.eu) for a custom Growth plan.

Memee is memory, not model. Value scales sublinearly with headcount — one canon serves the whole team — so pricing is flat, not per-seat.

---

## Memory, compared

Memory products cluster around two shapes: per-user context (ChatGPT, Hermes) or per-agent state (Letta, Mem0, Zep). Memee is the only one built per-organisation, with confidence scoring, cross-project propagation, and a flat-fee team tier.

| Product | Cross-model | Cross-project | Cross-team | Auto-capture | Aging / lifecycle | Licence |
|---|---|---|---|---|---|---|
| **Memee** | Claude, GPT, Gemini, Llama | Yes. Propagation engine | Yes. Team / org scope | Both. Quality gate | Confidence + 60-day archive | MIT (OSS) + EULA (Team) |
| Claude Skills | Open standard. Ported to GPT, Gemini | Per-folder. Manual reuse | No. Static files | No. Author-written | None. Files don't decay | Free with Claude |
| Mem0 | Claude, GPT, Gemini, local | Not first-class | Cloud tier | Auto-extract from chat | Not documented | Apache 2.0 + cloud from $19 |
| Letta | Multi-model | Per-agent state | Not advertised | Auto via agent loop | Three-tier virtual memory | Apache 2.0 + cloud from $20 |
| Zep | Multi-model | Not a documented primitive | Roles via API | Auto from episodes | Temporal graph | Graphiti Apache 2.0. Cloud from $25 |
| ChatGPT Memory | Vendor-locked to ChatGPT | Project containers. Isolated | No. Per-user only | Both | Not published | Bundled with Plus / Team |
| Hermes Agent | Provider routing. Multi-LLM | Single-server posture | No | Both. Agent-curated | Not documented | MIT |

Sources, verified April 2026: [anthropic.com/news/skills](https://www.anthropic.com/news/skills), [mem0.ai/pricing](https://mem0.ai/pricing), [letta.com/pricing](https://letta.com/pricing), [getzep.com](https://www.getzep.com/), [openai.com/memory](https://openai.com/index/memory-and-new-controls-for-chatgpt/), [hermes-agent.nousresearch.com](https://hermes-agent.nousresearch.com/).

---

## Three fair questions

From the launch thread. Answered without flinching.

### Doesn't an MCP server just add to my context window?

It would, if it dumped. Memee doesn't. The router has a hard 500-token cap and lands at **~40 tokens** on average per briefing. Measured, not aspirational.

Compare that to the file you already ship: median `CLAUDE.md` / `AGENTS.md` across 27 popular OSS repos is **~2,160 tokens**, loaded in full on every session and ridden along on every turn. That file only grows. Memee is the cure for context bloat, not a new source of it.

### Don't Claude Skills already do this?

Skills are good. They're also Claude-native, hand-authored, static, and per-folder. Memee is none of those things.

It writes from the conversation, not from your hand. It scores what it captures (cross-model ×1.3, cross-project ×1.5). It ages out what stops earning its keep on the lifecycle ladder, *hypothesis → tested → validated → canon → deprecated*. The same canon answers GPT in Cursor, Gemini in Continue, and Llama wherever you run it. Skills are a folder. Memee is a memory.

### How is this different from Mem0, Letta, Zep, ChatGPT memory?

Most memory projects remember *conversations*: you talked about X last Tuesday, here it is again. That's chat history with a vector index.

Memee earns *canon*. A claim arrives at 0.5 confidence and goes nowhere until a second model family agrees and a second project re-uses it. On OrgMemEval v1.0 that capability gap shows up as **92.3 %** against a competitor baseline of **~2 %**. Not because the others are bad. Because they aren't built for the job.

Conversation memory remembers what was said. Institutional memory remembers what was learned.

---

## Contributing

PRs welcome. Before opening a large one, a short issue describing the direction saves everyone a round-trip.

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Style: type hints, English docstrings, 100-char lines, `ruff` clean. New engines live in `src/memee/`. Every new behaviour wants a test in `tests/`.

---

## License

Memee core is [MIT](LICENSE). The optional `memee-team` package is proprietary, distributed under a separate commercial EULA. See [memee.eu](https://memee.eu) for the terms.

---

**Built by people who stopped teaching the same lesson to every new agent.**
