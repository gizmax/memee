# v2.0.0 launch copy — drafts for site, README, CHANGELOG

The v2.0.0 release is the simplicity-and-loop-disappear release.
Five things changed, one big thing got deleted.

## Hero variants for the homepage swap

### Option A — keep current "You shouldn't have to teach it twice."

Don't change the hero. v2.0.0 is a backstage release; the message
hasn't shifted. Site eyebrow bumps `v1.2.0 → v2.0.0`. That's it on
the hero level.

### Option B — surface the "loop disappears" promise

```
You shouldn't have to teach it twice.
And after v2.0.0 you won't have to call it, either.
```

Small drop-in below the existing hero subhead. Adds the new feature
without rewriting the headline.

**Recommendation: Option A.** The hero earned its keep across
v1.0.x; don't churn it. Mention the loop in the new sections.

## Compare table — update one row

The Compare section has Memee already at the top. Update one cell
in `Auto-capture` to acknowledge what hooks did:

Current: "Both. Quality gate"
New:     "Both. Hooks fire it. Quality gate guards it."

That's it for the compare. Everything else stays factually right.

## FAQ — add a fourth card

Three current cards (MCP context, Skills, Mem0/Letta/Zep). Add a
fourth:

```
Q4. So I install it and… it just works?

After memee setup, yes. Hooks land in your Claude Code settings:
SessionStart fires a routed briefing into the agent's context.
UserPromptSubmit fires a task-aware brief on every prompt. Stop
fires a post-task review. Three lines in your settings.json.
You don't write them; memee setup writes them. You don't call
memee_search; the hook called it.

You can opt out (memee setup --no-hooks) and use Memee as a
plain MCP tool. Most don't.

Kicker: the agent doesn't have to remember Memee exists.
The runtime injects what it needs.
```

## What v2.0.0 actually changed (for the changelog narrative)

The release shipped six things:

1. **Hooks. The loop disappears.** memee setup writes
   SessionStart/UserPromptSubmit/Stop hooks into settings.json so
   every Claude Code session starts already knowing.

2. **Cross-encoder rerank by default.** When the model is already
   in your HF cache, rerank fires automatically. +0.0355 macro
   nDCG@10 on the 207-query harness, transparent to the user.

3. **Memory packs.** `.memee` portable bundle format. Two seed packs
   ship: python-web (30 entries) and react-vite (28 entries). One
   command to import. Solves cold start.

4. **`memee why "<code>"`.** New CLI: pastes code, returns the canon
   that would have prevented or explained it. The screenshotable
   demo moment.

5. **Citation tokens.** Briefings include a footer instructing the
   agent to cite memories it applies via `[mem:abc12345]`. `memee
   cite <id>` resolves the lineage. Soft validation pathway.

6. **−2,400 LOC.** Web dashboard, autoresearch engine, and three
   dead substrate modules removed. Two schema tables dropped via
   migration. Six MCP tools deleted (every session was paying
   tokens for tools nobody called).

The release is a major version bump because:
- Schema removed (ResearchExperiment, ResearchIteration tables)
- 6 MCP tools deleted
- Web dashboard endpoints removed
- `memee research` CLI gone

## CHANGELOG entry skeleton

```markdown
## [2.0.0] — 2026-04-27

The loop-disappears + simplicity-reclamation release. Hooks land,
packs ship, dashboard goes, autoresearch goes. Six MCP tools fewer,
~2,400 LOC fewer, ~+0.0355 nDCG@10 by default.

### Added

- **Hooks in `memee setup`.** SessionStart, UserPromptSubmit, Stop
  hooks land in `~/.claude/settings.json`. Every session starts with
  a routed briefing; every user prompt triggers a task-aware brief;
  Stop runs the post-task review. Idempotent merge (preserves your
  hooks), `--no-hooks` opt-out, `--dry-run` preview, `memee doctor
  --uninstall-hooks` removal.
- **`.memee` pack format.** Portable, optionally-signed bundle:
  `manifest.toml + memories.jsonl + (signature.bin + pubkey.pem)`.
  `memee pack export | install | list | verify`. Two seed packs
  ship: python-web (30 entries), react-vite (28 entries). Imports
  go through the existing quality gate; user canon outranks pack
  defaults via source_type=import multiplier and confidence cap.
- **`memee why "<code>"`.** New CLI: pipe in code, get back the
  canon that would have prevented or explained it. Top 3 hits with
  IDs, severities, alternatives.
- **`memee cite <id>`.** Resolves an 8-char hash (or full UUID) to
  full lineage: who recorded, what validated, when promoted to canon.
- **Citation tokens in compact briefings.** Footer instructs the
  agent to cite applied memories with `[mem:abc12345]`. Soft
  validation pathway.

### Changed

- **Cross-encoder rerank is default-ON when the HF cache is warm.**
  No env var required for the +0.0355 macro nDCG@10 lift on the
  207-query / 255-memory eval harness. Three escape hatches:
  `MEMEE_RERANK=0` kill switch, `MEMEE_RERANK_MODEL=<id>` explicit
  override, and the cache probe is read-only (never downloads).
- **`memee doctor`** reports rerank status with one of: `enabled
  (cached)`, `enabled (env)`, `disabled (no model cached)`,
  `disabled (kill switch)`. The disabled-no-model branch ships an
  actionable hint.
- **`installer.py` post-setup screen** is honest about hooks. It
  used to claim "Memee is now live and fully automatic" before any
  hooks existed; v2.0.0 makes the claim true.

### Removed

- **Web dashboard.** `api/routes/dashboard.py` (~556 LOC), `memee
  dashboard` and `memee serve` CLI commands gone. The product pitch
  ends with "no dashboards, no copilots, no magic" — now the
  codebase agrees. Use `memee status` for the same numbers in your
  terminal.
- **Autoresearch engine.** `engine/research.py` (~641 LOC) +
  `tests/test_research_engine.py` + `tests/bench_autoresearch.py`
  + 6 CLI subcommands + 5 MCP tools + 2 schema tables
  (ResearchExperiment, ResearchIteration). It was a Karpathy-style
  self-improvement harness, beautifully built and entirely
  orthogonal to "institutional memory." Every session was paying
  tokens for MCP tools no agent ever called. Migration
  `5b8c2f1d4e9a` drops the tables; existing data is destroyed
  (this is a clean cut).
- **`engine/canon_ledger.py`, `engine/evidence.py`, `engine/tokens.py`**
  (~830 LOC of substrate with zero production callers). The
  `evidence_chain` JSON column on `Memory` (used by `quality_gate`
  dedup) stays. The dead modules go.
- **`LearningSnapshot` schema** + `/api/v1/snapshots` endpoint. Was
  written only by `demo.py`; queried only by the deleted dashboard.

### Migration notes

If you're already running Memee from a previous version:

```bash
pipx upgrade memee
memee doctor               # installs hooks; reports rerank status
memee pack install python-web  # day-one canon
```

The schema migration (`5b8c2f1d4e9a_drop_research_tables`) drops
two tables. Their data was internal to the autoresearch engine —
no user-recorded memory is touched.

### Breaking

- `memee dashboard`, `memee serve`, `memee research *` commands
  removed. If you scripted against them, pin to v1.x.
- 5 MCP `research_*` tools removed. Agents that called them will
  see "tool not found"; nothing else is affected.
- REST `/api/v1/research/*` and `/api/v1/snapshots` endpoints
  removed. `fastapi` and `uvicorn` moved from base dependencies to
  the optional `[api]` extra.
```

## X.com post copy

Three options ranked by daring:

### Option 1 — facts only (safe)

> Memee v2.0.0 ships.
>
> • Hooks in memee setup → loop disappears
> • Cross-encoder rerank default-on (cached) → +0.0355 nDCG@10
> • .memee pack format → python-web + react-vite seed packs ship
> • memee why "<code>" → ask Memee to roast your last commit
> • −2,400 LOC: dashboard out, autoresearch out
>
> pipx upgrade memee && memee doctor

### Option 2 — narrative

> Made it Friday's job to delete things from Memee.
>
> Out: web dashboard. autoresearch engine. three substrate modules
> nobody called. ~2,400 LOC.
>
> In: hooks (the loop disappears), .memee packs (cold start solved),
> memee why "<code>" (the screenshot moment).
>
> v2.0.0. pipx upgrade memee.

### Option 3 — provocative

> The 489th memory project for AI got smaller this week.
>
> Memee v2.0.0 deleted the dashboard, deleted the autoresearch
> engine, and added the thing every memory product needs and nobody
> ships: a portable .memee format you can install in one command.
>
> Plus hooks in memee setup. Your agent stops asking. The runtime
> injects what it needs. The loop disappears.
>
> pipx upgrade memee. memee.eu.

**Recommendation: Option 2.** Honest, narrative, doesn't oversell.
Save Option 3 for the second wave once the release lands.
