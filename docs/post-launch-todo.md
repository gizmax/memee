# Post-launch TODO

Items deliberately deferred past the first public launch. Published so prospects and customers see the known gaps.

## Engine

- [ ] **`sqlite-vec` ANN adapter.** Current vector search loads all embeddings into Python and scales cleanly to ~50 k memories. Beyond that, memory pressure rises. Drop-in replacement with `sqlite-vec` or `sqlite-vss` is straightforward; target: p50 < 25 ms at 500 k memories.
- [ ] **Confidence intervals on simulations.** All simulation tests (NovaTech, TechCorp, MegaCorp, GigaCorp) run deterministically. For a formal whitepaper, run each scenario N times with different seeds and report mean ± 95 % CI.
- [ ] **Better dedup clustering.** Scope-aware thresholds + cluster-size cap land at team / org scope with sensible defaults, but a real-world calibration run on customer data will likely need per-domain tuning. Instrument the merge-decision logger first (already emits to `evidence_chain`), then analyse after two months of live data.

## Product

- [ ] **Evidence ledger enforcement in the wild.** `mistakes_avoided` requires an evidence ref to count. Getting real-world agents to emit those refs consistently is adoption work, not code work. Watch the `warnings_acknowledged` vs `mistakes_avoided` gap in the dashboard — the wider it is, the more coaching / MCP-tool-hinting customers need.
- [ ] **Better retrieval feedback loop.** `memee feedback` and `search_feedback` MCP tool exist; adoption will be zero unless agents are nudged to call them. Add auto-feedback heuristics: when an agent writes code referencing a returned memory's title, auto-mark it accepted.
- [ ] **Memory expiry signal.** `expires_at` is in the schema but never set automatically. Add rules: a memory whose anti-pattern trigger hasn't fired in 180 days suggests the trigger no longer exists (library fix, language change). Flag for review.

## Infrastructure

- [ ] **Migration story for existing OSS users upgrading to memee-team.** When a solo user pays and installs `memee-team`, their existing memories should remain theirs (owner = the user the memee-team setup creates). Needs a clean one-shot migration: ask the user for their identity, stamp `owner_id` on every existing memory.
- [ ] **License key rotation.** Ed25519 offline-verify is fine for v1, but long-running deployments need a rotation mechanism. Signed bundle with old + new public keys, grace period.
- [ ] **Phone-home telemetry (opt-in).** Anonymous weekly ping from `memee-team` customers to confirm licence is active. Off by default, clearly documented, purely for sales / renewal reminders.

## Simulation / benchmark

- [ ] **Public OrgMemEval.** Our 8-scenario benchmark currently lives in the repo. Package it as a standalone CLI (`pip install orgmemeval-memee`) so competitors can self-score. Expected to further widen the 93.8 vs 2.3 gap because our competitors never see our scenarios.
- [ ] **Third-party replication of the A/B impact study.** The 71 % / 65 % / 96 % numbers are from an internal A/B simulation (7 tasks). Get an academic or independent lab to replicate with their own tasks. Budget: small grant to any CS department interested in organisational-memory-for-agents research.

## Security / compliance

- [ ] **SOC 2 Type I → Type II.** Enterprise tier promises SOC 2 Type II. Expect 6-9 months of evidence collection from first signup. Type I can ship in month 1; Type II gate by month 8 - 10.
- [ ] **Penetration test before GA.** Not required for beta, required before we claim "Enterprise-ready" in marketing.
- [ ] **SBOM publication.** Generate CycloneDX SBOM per release, publish at `memee.eu/sbom/<version>.json`.

---

Last updated: 2026-04-24.
