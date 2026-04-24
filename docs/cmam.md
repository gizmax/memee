# CMAM Bridge — Memee ↔ Claude Managed Agents Memory

> **TL;DR.** Anthropic's [Managed Agents Memory (CMAM)](https://platform.claude.com/docs/en/managed-agents/memory) is a filesystem-style store mounted at `/mnt/memory/` inside a Claude agent container. It is **Claude-only** and has **no intelligence** — no confidence, no dedup, no cross-project routing, no multi-model validation. Memee stays the brain; CMAM becomes the delivery mechanism for Claude sessions.
>
> **Use this bridge** when you want Claude agents running in Anthropic's managed environment to see Memee's validated organizational knowledge in `/mnt/memory/` from turn 1, without an MCP round-trip.

---

## 1. Why this bridge exists

Memee and CMAM solve **different problems**:

| Concern | Memee | CMAM |
|---|---|---|
| Multi-model (Claude / GPT / Gemini / Llama / …) | ✅ | ❌ Claude-only |
| Confidence scoring + maturity lifecycle | ✅ | ❌ |
| Quality gate, dedup, hallucination quarantine | ✅ | ❌ |
| Cross-project propagation | ✅ | ❌ |
| Token-budgeted task routing | ✅ | ❌ |
| Evidence ledger + changelog | ✅ | ❌ |
| Native filesystem access from a Claude session | ❌ | ✅ |
| Managed durability + 30-day version retention | ❌ | ✅ |
| Zero-MCP baseline context at session start | ❌ | ✅ |

They are **complementary**, not competing. The bridge lets Memee's intelligence feed CMAM's delivery path.

## 2. What gets synced

Only knowledge Memee has **earned the right to ship** reaches CMAM:

- **Canon memories** — maturity `CANON`, meaning confidence ≥ 0.85, validated in ≥ 5 projects, with ≥ 10 validations.
- **Critical anti-patterns** — severity = `critical`, pushed regardless of maturity because the cost of missing one dominates everything else.

Nothing with `deprecated_at` set is pushed. Nothing in `hypothesis` or `tested` maturity is pushed (except the critical-AP carve-out). The exclusion is intentional: CMAM has a hard cap of 2000 memories per store, so we burn quota on high-signal content only.

## 3. Store layout

```
/canon/patterns/<slug>.<id-prefix>.md    ← canon patterns
/canon/lessons/<slug>.<id-prefix>.md     ← canon lessons
/canon/other/<slug>.<id-prefix>.md       ← canon observations/misc
/warnings/critical/<slug>.md             ← critical anti-patterns
/warnings/high/<slug>.md                 ← high-severity anti-patterns
/warnings/medium/<slug>.md               ← medium-severity anti-patterns
/decisions/<slug>.<id-prefix>.md         ← recorded decisions
/_index.md                               ← human-readable index of the store
```

Every file is a markdown document with YAML front-matter that carries the machine-readable Memee metadata (id, type, maturity, confidence, validation_count, project_count, tags). The body is human-readable content an agent can lift into its reasoning verbatim.

Example rendered anti-pattern:

```markdown
---
id: 3f7c1b2a-...
type: anti_pattern
maturity: hypothesis
confidence: 0.74
validation_count: 4
project_count: 2
tags: [security, python]
source: memee
---

# Never eval() user input

eval() on untrusted input allows arbitrary code execution.

**Severity:** critical
**Trigger:** calling eval() on user-supplied data
**Consequence:** arbitrary code execution, RCE
**Alternative:** use ast.literal_eval or a proper parser
```

## 4. Configuration

All CMAM settings are under the `MEMEE_CMAM_*` prefix and optional by default (`cmam_enabled=False`).

| Env / setting | Default | Meaning |
|---|---|---|
| `MEMEE_CMAM_ENABLED` | `false` | Explicit opt-in. Not enforced at the adapter level today — the CLI/MCP call is the trigger — but reserved for auto-sync hooks. |
| `MEMEE_CMAM_BACKEND` | `fs` | `fs` writes markdown to a local directory; `api` calls Anthropic's managed memory endpoint. |
| `MEMEE_CMAM_STORE_ID` | `memee-canon` | Identifier for the target store. With `fs`, it becomes the subdirectory name under `~/.memee/cmam/`. With `api`, it is the managed store id. |
| `MEMEE_CMAM_LOCAL_ROOT` | `~/.memee/cmam/<store_id>` | Output root for the `fs` backend. Mount this into a container at `/mnt/memory/` to give a Claude session direct access. |
| `MEMEE_CMAM_API_BASE` | `https://api.anthropic.com` | Base URL for the `api` backend. |
| `MEMEE_CMAM_REDACT` | `true` | Strips common secrets from content before push (API keys, AWS access keys, GitHub PATs, GitLab tokens, Slack tokens). Leave on. |
| `ANTHROPIC_API_KEY` | — | Required for `api` backend. Standard Anthropic API key env var. |

CLI flags (`--store-id`, `--backend`, `--local-root`, `--api-base`) override settings per invocation.

## 5. Operating the bridge

### 5.1 Local filesystem (recommended for most setups)

```bash
# Write canon + critical APs to a local tree
memee cmam sync --backend fs --local-root ~/.memee/cmam/my-org

# Inspect what landed
memee cmam status --local-root ~/.memee/cmam/my-org

# Preview without writing — useful during first rollout
memee cmam sync --dry-run
```

Mount the root inside a managed agent container so it appears at `/mnt/memory/`:

```bash
# Example: Docker bind-mount
docker run --rm \
  -v ~/.memee/cmam/my-org:/mnt/memory:ro \
  my-claude-agent-image
```

The agent's `memory` tool (view / create / str_replace / insert / delete / rename) sees the tree as if CMAM had populated it. `ro` (read-only) is a sensible default — write access is only needed if the agent itself should be able to edit the store.

### 5.2 Anthropic managed API

```bash
export ANTHROPIC_API_KEY=sk-ant-...
memee cmam sync --backend api --store-id my-org-canon
```

> The `api` backend code path is wired but has not been end-to-end verified against a live managed-memory endpoint. Test on a non-production store id first. File an issue if the HTTP surface has shifted.

### 5.3 From inside a Claude session (MCP)

An agent with Memee's MCP server attached can trigger the push itself:

```
Tool: sync_to_cmam
Args: { "store_id": "my-org-canon", "backend": "fs", "dry_run": false }
```

Useful right after the agent records a promotion-worthy pattern and wants downstream sessions to pick it up immediately.

### 5.4 Suggested cadence

- **Nightly cron** after `memee dream` — the dream cycle is what promotes memories to canon, so sync right after.
- **On promotion** — add a post-promotion hook that calls `sync_to_cmam` with `dry_run=false` once a memory hits `CANON`.
- **On critical-AP recording** — any new `severity=critical` anti-pattern should be pushed immediately; losing a day on a critical warning is the scenario this bridge is designed to prevent.

## 6. Enforced limits

CMAM's documented caps are enforced locally so a run never produces a store the real service would refuse:

| Limit | CMAM | Memee enforcement |
|---|---|---|
| Per-memory size | 100 KB | Content ≥ 100 KB is auto-chunked into `<path>.part-1.md`, `.part-2.md`, … with front-matter preserved on every chunk. |
| Per-store size | 100 MB | Soft warning at 80 MB (surfaced in `warnings[]`); hard stop at 95 MB (rejected before write). |
| Memories per store | 2000 | Soft warning at 1600; hard stop at 1900. |
| Stores per org | 1000 | Not enforced — CMAM refuses creation past this point. |
| Stores per session | 8 | Not enforced — Claude runtime limit. |
| Optimistic concurrency | SHA256 precondition | Supported on both backends. `fs` compares file hash; `api` sends `If-Match: <sha256>`. Conflicts are reported as `status: conflict` without overwriting. |

## 7. Security

- **Path traversal:** the `fs` backend rejects paths containing `..`.
- **Secret redaction:** regex-based scrubbing runs on every rendered memory by default. Patterns covered today: OpenAI / Anthropic keys (`sk-…`), AWS access keys (`AKIA…`), GitLab tokens (`glpat-…`), GitHub PATs (`ghp_…`), Slack tokens (`xox[baprs]-…`). The list is intentionally conservative — add more via PR.
- **Scope awareness:** only memories Memee considers canon-grade reach CMAM. Personal-scope memories are never auto-promoted, which means they cannot leak into a shared CMAM store by accident.

## 8. Multi-model flow

The whole point of the split:

```
GPT-4 / Gemini / Llama agent records a pattern
  ↓
Memee quality gate (validate + dedup + source classify + quality score)
  ↓
Confidence accumulates via cross-project + cross-model validation
  ↓
Memory reaches CANON (≥0.85 confidence, ≥5 projects, ≥10 validations)
  ↓
`memee cmam sync` (or MCP `sync_to_cmam`)
  ↓
Claude agent session starts → reads /mnt/memory/_index.md → has org knowledge turn 1
```

Memee stays model-agnostic. CMAM becomes the Claude-native consumption surface for the subset of knowledge that has earned trust across the whole org.

## 9. What this bridge is *not*

- **Not a sync of personal / team scope.** Only org-level canon + critical APs are shipped.
- **Not a live mirror.** It is a one-way push initiated by CLI, MCP, or cron. CMAM edits made by a Claude session are not pulled back into Memee (a reverse-sync would need its own quality gate and is out of scope for v1).
- **Not a replacement for Memee.** Agents using non-Claude models, or needing token-budgeted routing / scope filtering / confidence queries, still go through Memee directly via MCP / CLI / REST.
- **Not a workaround for low-quality memories.** Nothing subcanon reaches CMAM. If you expect a memory there and don't see it, check `memee status` — it is almost always a maturity / confidence issue.

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `memee cmam sync` reports `pushed: 0` | No canon memories yet. | Run `memee dream` to trigger promotion; confirm with `memee status` or `memee benchmark`. |
| File count does not match memory count | Long content was chunked into `.part-N.md`. | Expected — each part is a separately-addressable file in CMAM. |
| `conflict` status on a file | SHA256 precondition mismatch — someone else (or an earlier agent) edited the file. | Re-run sync; the adapter will detect and update. If conflicts persist, inspect with `memee cmam status`. |
| `ANTHROPIC_API_KEY` required error | `--backend api` selected without the env var. | `export ANTHROPIC_API_KEY=…` or switch to `--backend fs`. |
| Store size near 95% hard limit | Too many low-value canon entries, or memories not being deprecated. | Run `memee dream` (handles aging + invalidation-ratio deprecation). Consider splitting into multiple stores by domain (`memee-canon-backend`, `memee-canon-frontend`, …). |
| `path traversal blocked` error | A memory title produced a slug that collided with `..` — should not happen with the current slug regex; file a bug. | — |

## 11. Operational checklist

- [ ] Decide backend (`fs` for most; `api` if the org is in Anthropic's managed environment).
- [ ] Pick one store id per logical knowledge boundary (org-wide is fine; split by domain only when hitting caps).
- [ ] Add `memee cmam sync` to the nightly cron right after `memee dream`.
- [ ] Verify first run with `--dry-run`, then full sync, then `memee cmam status`.
- [ ] For `fs`, confirm the mount is `:ro` unless agents should write back.
- [ ] For `api`, keep `ANTHROPIC_API_KEY` in a secret store, not in shell history.
- [ ] Leave `MEMEE_CMAM_REDACT=true`; add regex patterns if your org has custom token formats.

## 12. References

- `src/memee/adapters/cmam.py` — implementation.
- `tests/test_cmam_adapter.py` — 18 tests covering mapping, chunking, SHA256 preconditions, path traversal, byte/count limits, idempotency, dry-run.
- `src/memee/cli.py` — `memee cmam sync`, `memee cmam status`.
- `src/memee/mcp_server.py` — `sync_to_cmam` MCP tool.
- `src/memee/config.py` — `MEMEE_CMAM_*` settings.
- Anthropic docs: [Managed Agents Memory](https://platform.claude.com/docs/en/managed-agents/memory).
