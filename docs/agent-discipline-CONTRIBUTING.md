# Contributing to `agent-discipline`

This pack is the editorial outlier. The other Memee seed packs
(`python-web`, `react-vite`, `mcp-server-canon`, `http-api-canon`)
catalogue **technical failure modes** in a stack — wrong API call,
wrong index type, wrong status code. `agent-discipline` catalogues
**agent decision failure modes** — wrong inference, wrong claim,
wrong consent. The format is the same. The substrate isn't.

The pack survives only as long as it stays universal-truth canon. The
moment it accepts preferences, voice rules, or shop conventions, it
becomes another team's CLAUDE.md and the brand is gone.

This document is the gate.

## Reject criterion

**Every accepted entry must cite a named failure mode in primary
literature.** No citation, no entry. The acceptable sources, in
descending authority:

1. **Anthropic Claude Code documentation** — *Best practices*, *Effective
   context engineering for AI agents* (Sep 2025), *Agent SDK* writeups.
   These are vendor-published, name failure modes explicitly, and reflect
   measurable behaviour of production agents.
2. **Academic taxonomies of LLM agent hallucinations / failures** —
   primarily arXiv 2509.18970 ("LLM-based Agents Suffer from
   Hallucinations: A Survey of Taxonomy, Methods, and Directions",
   Sep 2025) and successor surveys. The 5-class taxonomy
   (Reasoning / Execution / Perception / Memorization / Communication)
   is the structural anchor.
3. **Public AI incident reports** — AI Incident Database, named
   postmortems. The Replit/SaaStr Jul 2025 production-database deletion
   (Incident #1152) is the canonical "ignored explicit freeze, lied
   about rollback" case. Memee will accept incidents only when they
   are publicly documented; private postmortem hearsay is rejected.
4. **Named protocols and named-author essays with traction** — Simon
   Willison's *lethal trifecta* (Jun 2025), `sycophancy.md` protocol,
   Karpathy's context-engineering threads when archived in a stable
   form. Author + URL + date required in the citation.
5. **Tool-vendor agent guidance** — Cline, Cursor, Continue, Aider,
   OpenAI cookbook. Lower than 1-4 because it's commercially aligned;
   accepted only when it converges with one of the higher-authority
   sources.

If your entry's strongest source is a tweet, a Reddit thread, or
"everyone agrees," it does not ship. Memee will not paraphrase
opinion as canon.

## What gets rejected on sight

Any entry whose negation is "we don't do that here" rather than
"that is incorrect." Concrete examples that **must not** ship as
`agent-discipline`:

- Anything about **tone** (terse, formal, casual, no emojis).
- Anything about **formatting** (markdown, bullet density, code blocks).
- Anything about **language** (English, Czech, English-only comments).
- Anything about **convention** (PR shape, branch naming, commit style).
- Anything about **stack preference** (use TypeScript, prefer functional
  components, use Postgres). Those belong in stack-specific packs.
- Anything about **process** (always open PR, never push to main, run
  CI before merge). Those vary by team and tool.
- Anything that names a **single model** ("Claude does X better
  than GPT"). Memee canon is cross-model by construction.

If an entry feels like it teaches a model to behave like *your team
likes* rather than *not be wrong*, it's CLAUDE.md territory.

## Voice rules (verbatim from existing packs)

- **Anti-patterns**: structured `Trigger / Consequence / Alternative`
  with `severity` (low / medium / high / critical). The `Trigger`
  here describes a *cognitive state* (e.g. "you don't recall the
  file and the search would take three tool calls"), not a syntactic
  one. The `Consequence` cites or summarises the failure mode from
  the literature.
- **Patterns / lessons**: structured `Why` and `When`. Shorter than
  anti-patterns; one positive practice per memory.
- **Decisions**: `Chose X. Alternatives considered: Y, Z. Reason.
  Reversible: Yes/No.`
- **Title**: ≥10 chars, actionable, names the rule (not the topic).
- **Content**: ≥15 chars; explains WHY and WHEN; cites the source
  inline or in `evidence_chain`.
- **Tags**: lowercase, kebab-case, technology-first. The
  `agent` tag is mandatory on every entry. Domain tags after:
  `verification`, `honesty`, `destructive`, `scope`, `sourcing`,
  `sycophancy`, `lethal-trifecta`, `tool-use`, `recovery`, `consent`.
- **One claim per memory**. No stacking ("never X and never Y" — split
  into two entries).
- **Madison Avenue 1962 voice**: confident, terse, no marketing,
  no hedging.

## Confidence + maturity

- All seed entries cap at `confidence: 0.6` (matches the manifest's
  `confidence_cap`).
- `maturity: canon` is reserved for entries with multi-source
  consensus AND a public incident or measured study behind them
  (e.g. fabrication, freeze-violation, sycophancy).
- `maturity: validated` for solid rules with one strong source.
- `maturity: tested` only when the source canon is still emerging.
- `maturity: hypothesis` is **not allowed** in this pack. If you
  cannot confirm, the entry is not pack-worthy.

## Editorial process for new entries

1. **Open an issue, not a PR.** Title: `agent-discipline: <rule>`.
   Body: the entry as JSON + the citation URL + one paragraph
   explaining why this is universal-truth canon and not preference.
2. The maintainer responds with one of:
   - **Accept** — open the PR.
   - **Re-cite** — your source isn't authority-grade; here's what
     would qualify.
   - **CLAUDE.md territory** — the rule is real but team-specific;
     it doesn't ship in this pack.
   - **Already covered** — entry overlaps with an existing one;
     here's the existing slug.
3. PRs without an accepted issue are closed without review. This is
   not gatekeeping; it's the cost of keeping the canon credible.
4. The maintainer publicly closes ~80 % of submitted entries. The
   rejection rate is a feature. The pack stays small (target 30-50
   entries; hard cap 60).

## Drift detection

Once a quarter, run a self-audit:

- Spot-check 5 random entries. Re-verify the citation still resolves.
  Citations rot.
- Spot-check 5 random entries. Could any of them be argued as
  "we don't do that here" rather than "that is incorrect"? If yes,
  deprecate the entry and document the reasoning.
- Re-read the academic taxonomy paper. If a new edition has named a
  failure class the pack doesn't cover, add it. If the pack covers
  something the taxonomy has retired, deprecate.

The pack ages slowly by design. The half-life of integrity rules is
much longer than the half-life of stack rules. But the citations age
faster than the rules — keep the URLs alive.

## Why this matters

The pack's credibility is its only asset. A stack pack survives
contributor drift because the language has its own ground truth —
`eval()` is dangerous regardless of who said so. The agent-discipline
pack has no such anchor. Its truth is the cited literature. The day
the pack accepts an entry without authority, the citation chain
breaks, and the whole pack is "Memee's opinion." That is not a brand
position the project can recover from.

If you can't commit to this editorial discipline, don't contribute.
The pack is meant to be small, citable, and slow-moving. That's the
whole shape.
