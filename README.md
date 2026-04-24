# Memee

[![tests](https://img.shields.io/badge/tests-passing-brightgreen)](#tests)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![pypi](https://img.shields.io/badge/pypi-memee-orange)](https://pypi.org/project/memee/)

**Institutional memory for AI agent teams. Your agents stop re-solving problems.**

Memee sits between your agents and the work. It records what worked, flags what didn't, and hands each agent only the 5–7 memories it actually needs for the task in front of it — instead of re-stuffing 500 patterns into every prompt.

```bash
pipx install memee            # recommended for a CLI tool
# or:
python3 -m pip install memee  # if you don't have pipx
```

> **For teams and companies.** This OSS release is single-user and
> self-hosted. If you want the same memory shared across your whole
> team, with cross-developer / cross-agent / cross-project / cross-model
> canon building into **company-wide institutional knowledge**, there's
> a paid Team edition at [memee.eu](https://memee.eu). Same engine,
> plus SSO, audit log, and shared scope. From $49 / month flat (up to
> 15 seats), $12k / year Enterprise with SOC 2 + air-gap.

---

## Why Memee

- **Your agents stop repeating mistakes.** One agent hits a bug, records it, every other agent (in every other project) is warned before it happens again.
- **Send ≤500 tokens, not 14,550.** A smart router picks the memories relevant to the current task inside a configurable token budget. Internal sim: 96%+ reduction per task; measured router output on a 500-pattern corpus averages ~40 tokens, well below the budget cap.
- **Every model reads from one canon.** Claude, GPT, Gemini, Llama all record into, and pull from, the same memory. Cross-model agreement raises confidence; single-model claims stay provisional.

## Install and first use (60 seconds)

```bash
pipx install memee
memee setup

# Record a pattern you just learned
memee record pattern "retry with jitter" \
  --tags reliability,http \
  -c "Always use exponential backoff with jitter; capped at 30s; retry only idempotent verbs."

# Search it back
memee search "retry"

# Health check + auto-wire Claude Code / MCP config
memee doctor
```

That's it. Memory lives in `~/.memee/memee.db`. No account. Core read/write is fully local; optional vector embeddings use `sentence-transformers`, which fetches a ~80 MB model from HuggingFace on first use (skipped when `TRANSFORMERS_OFFLINE=1` is set).

## What it actually does

Memee is a stack of small engines sitting on SQLite + FTS5 + sentence-transformer embeddings.

| Layer | What it does | Why you care |
|---|---|---|
| **Router** | Task-aware briefing. Picks 5–7 memories within a token budget. | Agents get signal, not a dump. |
| **Quality gate** | Validates, dedupes, rates every incoming memory. | Junk doesn't survive the first write. |
| **Confidence scoring** | Adaptive: cross-project ×1.5, cross-model ×1.3, combined ×1.95. | Patterns earn trust across evidence, not by author claim. |
| **Lifecycle** | hypothesis → tested → validated → canon → deprecated. | Old advice ages out; good advice gets promoted. |
| **Dream mode** | Nightly: connect related memories, surface contradictions, promote canon. | Knowledge compounds while you sleep. |
| **Propagation** | A validated pattern auto-pushes to other projects with matching stack/tags. | Fix once, benefit everywhere. |
| **Review** | `git diff | memee review -` scans changes against anti-patterns. | Institutional memory enters code review. |
| **CMAM bridge** | Push canon to Anthropic's Managed Agents Memory at `/mnt/memory/`. | Claude sessions see canon on turn 1, no MCP roundtrip. |

Deeper architecture doc: [CLAUDE.md](CLAUDE.md). CMAM specifics: [docs/cmam.md](docs/cmam.md). Review engine: [docs/review-fixes.md](docs/review-fixes.md).

## Benchmarks

> All numbers below are **internal simulations and benchmarks**, not independent third-party evaluations. They describe system behaviour under synthetic workloads. Treat them as suggestive, not conclusive.

- **Token savings per task:** full-library baseline ≈14,550 tokens → router output capped at 500 tokens (≥96% reduction). Measured router average on a 500-pattern corpus is ~40 tokens per briefing (~99%), because the router stops at relevance, not at the cap.
- **OrgMemEval v1.0:** 92.4/100, across propagation, avoidance, maturity, onboarding, recovery, calibration, synthesis, and research. Competitor baselines on the same scenarios range from 0.9/100 (MemPalace) to 3.5/100 (Mem0, the closest); see [docs/benchmarks.md](docs/benchmarks.md).
- **7-task A/B (with vs. without Memee):** time –71%, iterations –65%, quality 56% → 93%, impact-DB ROI ≈ 10.7× at the current $49/mo Team tier. GigaCorp sim (100 projects, 100 agents, 18 months): incidents 12/mo → 3/mo, annual ROI ≈ 3× at the same flat Team tier.
- **Retrieval:** hit@1 = 100% on a 12-memory routing benchmark after the recent ranking fix.

Reproduce locally:

```bash
memee benchmark          # OrgMemEval v1.0
pytest tests/ -v         # full suite, ~60s
```

## Using it with Claude, GPT, Gemini

Memee ships an MCP server with 24 tools. Drop this into `~/.claude/settings.json` (or the Cursor / Continue / any MCP-capable client equivalent):

```json
{
  "mcpServers": {
    "memee": { "command": "memee", "args": ["serve"] }
  }
}
```

Memee auto-detects the caller's model family from `MEMEE_MODEL`, `ANTHROPIC_MODEL`, or `OPENAI_MODEL` and tags every write with `source_model`. That's how confidence scoring knows when Claude and Gemini agree — and when they don't.

Quick CLI:

```bash
memee brief --task "write unit tests"   # PUSH: routed briefing
memee check "about to add eval() here"  # PULL: anti-pattern check
memee propagate                          # cross-project diffusion
memee dream                              # nightly: connect, contradict, promote
memee cmam sync                          # push canon to /mnt/memory/ for Claude
```

## Pricing

**Memee (this repo) is MIT-licensed and free.** It's a single-user product: your memory, your machine, no account.

If you need multi-user scope (personal / team / org), SSO, audit log, seat management, or a hosted control plane, install `memee-team` — a paid proprietary package from [memee.eu](https://memee.eu). Pricing is flat, not per-seat: **$49/month** for a team of up to 15, **from $12k/year** for Enterprise with SOC 2, SCIM, and air-gap. It plugs into the same engine; no re-install, no data migration.

## Contributing

PRs are welcome. Before opening a big one, a short issue describing the direction saves everyone time.

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Style: type hints, docstrings in English, 100-char lines, `ruff` clean. New engines live in `src/memee/`; every new behaviour wants a test in `tests/`.

## License

Memee core is released under the [MIT License](LICENSE). The optional `memee-team` package is proprietary and distributed under a separate commercial EULA; see [memee.eu](https://memee.eu) for terms.
