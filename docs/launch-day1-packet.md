# Memee — Day-1 X.com launch packet (operator's manual)

Cílovka: ENG markets — AI builders, software engineers, indie hackers,
agent infrastructure. Cold open (zero existing users).

Tento dokument je **kompozit ze tří paralelních expert-agentů**:

- **Copywriter** (Draper / Ogilvy / Schafer) → `docs/launch-copy.md`
- **Launch strategist** → `docs/launch-strategy.md`
- **Creative director** → `docs/launch-creative.md`

V této packetě je destilát. Plné výstupy v jednotlivých souborech.

---

## ⭐ Headline rozhodnutí

Tři kandidáti, tři úhly:

| | Headline | Angle | Doporučeno |
|---|---|---|---|
| **A** | "Your agent forgot. Mine didn't." | Contrarian gut-punch — passive scroll → otázka ("did mine?") | **Copywriter pick** |
| **B** | "One canon. Every model." | Technical reveal — pro audiences co znají bolest cross-model | Tagline kandidát |
| **C** | "We stopped teaching the same lesson twice." | Founder voice — past tense, plurál, lived experience | Bio + thread closer |

**A** je nejlepší pro X cold scroll. Použij A na headline post; B jako tagline na site (`v1.2.0` eyebrow); C v bio + jako closer thread.

---

## ⭐ Day-1 X post (≤280 chars)

**Verze 1 — copywriter (emotional hook):**

```
Your agents forget. Every session, every project, every vendor swap — gone.

Memee writes it down once. Claude proves it. GPT confirms it. Gemini opens a different repo on a different team and starts already knowing.

One canon. Four model families. MIT.

pipx install memee
```

**Verze 2 — strategist (token-math hook):**

```
~2,160 tokens of CLAUDE.md per turn.
~40 tokens with Memee.

Same hit@1. Cross-model. MIT.

Methodology in repo (gh-api'd 27 OSS repos, 207-query eval).

  pipx install memee
```

**Doporučení:** **Verze 1 jako pinned post**, **Verze 2 jako 09:00 PT economics fresh top-level** (strategista to navrhuje jako Thread 2). Obojí současně dává cold-scroll emotional hook + analytical credibility — různá publika obou kategorií.

---

## ⭐ Den 1 schedule (T-0)

Strategista navrhuje Tuesday/Wednesday, never Mon/Fri. Times v PT/CET.

| PT | CET | Akce |
|---:|---:|---|
| 05:30 | 14:30 | Final dry-run: `pipx install memee`, screenshot briefing output |
| 05:55 | 14:55 | Update bio, confirm github public, releases v1.2.0 |
| **06:00** | **15:00** | **Headline post fires (Verze 1)** + creative asset |
| 06:05 | 15:05 | Reply to own headline post with thread post 1/8 |
| 06:30 | 15:30 | Show HN submission. *No reply* for first 30 min |
| 06:35 | 15:35 | QT vlastní headline: "HN thread is up if you'd rather argue there: [link]" |
| 07:00 | 16:00 | r/LocalLLaMA post — submit only, no comment |
| 07:15 | 16:15 | Reply k prvním 3 substantive HN komentářům — vždy s číslem |
| 08:00 | 17:00 | r/programming post |
| 09:00 | 18:00 | **Verze 2 economics post** jako fresh top-level (ne v threadu) |
| 10:30 | 19:30 | "First three hours: N installs, M stars, K HN points." Honest signal |
| 12:00 | 21:00 | Screenshot most surprising HN comment + reply — surfaces diskuzi non-HN audiences |
| 14:00 | 23:00 | EU dinner-time post: one technical detail (např. MinHash LSH 6.7×) |
| 16:00 | 01:00+1 | Founder logs off. **Do not reply for 12 hours.** |

---

## ⭐ Visual asset (lock-in: Prompt B)

**Format**: 1:1 still image (1080×1080, render at 2048×2048, downsample). Žádné video (dev audience reads motion graphics jako marketing flash; X auto-plays muted).

**Concept**: Memee jako *receipt that copied itself*. Index card uprostřed se "CANON" stampem, za ním 3 softer kopie pootočené 4-7°, vázané **jediným magenta vláknem** skrz děrované otvory jako ledger string. Tactile, archival, Anthropic-PR voice.

**Generation prompt (lock — Prompt B):**

```
A 1:1 editorial still life on deep graphite (#0E1116), shot from
slightly above with the perspective of a passport-control desk. One
matte off-white index card sits dead centre, set with the same
typography as A: "retry with jitter" in Inter Tight, model and repo
metadata in JetBrains Mono, a cyan-mint (#00E5C7) "CANON" rubber
stamp in the upper-right with a checkmark. The stamp is slightly
imperfect — ink density varies, edges are not pixel-clean — like a
real ink stamp on paper. Behind the front card, three identical
cards lie at staggered angles, each rotated 4–7 degrees off-axis,
each carrying a different model badge in its own ink colour: GPT in
graphite, Gemini in graphite, Llama in graphite (no colour-coded
models — all neutral). A single hot magenta (#FF4D8F) thread (1 px,
crisp, not glowing) runs from a hole-punch in the front card,
threading through hole-punches on the three behind it, like a
ledger string binding receipts. Bottom-edge typography: "memee —
one lesson. every agent. every team." Aesthetic: Anthropic PR
illustration meets Stripe Press cover. Tactile, archival, slightly
analogue. The thread is the only saturated colour. Everything else
is graphite, paper, off-white.
```

**Negative prompt:**
```
glowing thread, neon, chrome, futuristic, holographic, 3d render,
robot, AI cliché, hands, terminal, gradient mesh, cinema 4d, blender
default, lens flare, bokeh, vignette, dramatic lighting,
photorealistic skin, cartoon, doodle, whiteboard
```

**Alt text** (118 chars, post body line):
> *"A single CANON-stamped index card centred on graphite, with three softer copies behind it — one lesson, four agents, four readers."*

**Reserve assets:**
- Prompt A (safer, Linear/Vercel polish) — pokud B nerendere clean
- Prompt C (perforated dot-matrix receipt, weirdest defensible) — pro week-2 follow-up
- Vector typeset terminal-artefact fallback (38-token briefing vs 2,160-token CLAUDE.md baseline) — pokud renders fail entirely

---

## ⭐ 5 thought-patterns co má hook spustit

Strategista identifikoval 5 *intellectual provocations* co dělají senior engineera stop:

1. *"Wait, is that median actually right?"* — token-math číslo (~2,160) je defensible přes `gh-api'd 27 OSS repos`
2. *"I've felt this frustration"* — same retry rule re-discovered v 3 repos
3. *"OK this person's IR-literate"* — `nDCG@10`, `permutation_test`, `MRR` — language patří k seriózní IR
4. *"`pipx` not `pip`"* — small competence signal
5. *"I could replicate this in 20 min"* — barrier je install command, ne sign-up flow

---

## ⭐ Amplification path (zero budget)

### Reply hooks T-1 → T-0 — accounts to comment under (genuine, not "great post")

| Account | Topic angle | Risk |
|---|---|---|
| @swyx | dev tools / agent infrastructure | Low — Latent Space readers are exact audience |
| @simonw | LLM tooling, Datasette, open source | Low — Simon RT's well-engineered OSS |
| @hwchase17 | LangChain, agent infrastructure | Medium — competitive overlap (Mem0/LangMem) |
| @karpathy | AI fundamentals, education | High reward / low likelihood — only if technical post in last 24h |
| @dzhng / @nutlope | Vercel AI, indie agent builders | Medium — same audience |

**Pravidlo**: jeden technical comment za den, žádné self-promo. Memee se zmiňuje jen pokud relevant na konkrétní question.

### Channel drops Day-1

| Channel | When (PT) | Title | Risk |
|---|---|---|---|
| HN Show | 06:30 | "Show HN: Memee – cross-model shared memory for AI agent teams (MIT)" | Medium — HN audience tough on token-math claims |
| r/LocalLLaMA | 07:00 | tech-focused copy from `launch-posts.md` | Low — friendly to open-source memory |
| r/programming | 08:00 | variant — focus on agent loops vs CLAUDE.md | Medium |
| Lobsters | only if invited | same as HN | Low |

### "Show HN comment-1-by-author" (strategista's script):

```
Author here. Three things up-front:

(1) Honest negative result we shipped: field-aware BM25 weights regressed
    on our own eval harness (-0.0058 nDCG@10, p=0.32). Repo has the
    full sweep at tests/r14_bm25_weights_sweep.py. The hypothesis I
    started with didn't pan out; ranking work moved to cross-encoder.

(2) Methodology is reproducible: `python -m tests.retrieval_eval`
    on a 207-query × 255-memory harness with 7 difficulty clusters.
    Permutation test n=10000 for any claim of significance.

(3) The 2,160-token CLAUDE.md median is gh-api'd from 27 popular
    OSS repos (langchain, vercel/ai, prisma, zed, …). p95 is 9,600
    tokens; one outlier at 42k. Code at scripts/claudemd_audit.py.
    If yours differs — that's the point.
```

**Falsifiability je trust signal.** Show HN audience punishes perfect numbers; rewards honest negatives.

### Skip Product Hunt (strategista's defense)

Memee + PH = mismatch:
- PH audience je more designer / no-code than developer
- PH launch optimizes for upvotes via reciprocity / PH community ties (which we don't have)
- PH banner-and-tagline is a different format than HN credibility post
- Day-1 attention is finite; HN + Reddit + X is enough for ENG market

PH může jít později (Q3) jako re-engage motion s podstatně lepším asset.

---

## ⭐ Days 2-7 second wave

| Day | Topic | Template | Why this day |
|---|---|---|---|
| **Day 2** | "What shipped" technical post | Diff-style: before/after with one defensible number | Day 1 momentum still flows |
| **Day 3** | Honest negative — R14 #1 (field-aware BM25 didn't ship) | "We tried X. It didn't work. Here's the data." | High-credibility signal pro launching account |
| **Day 4** | First-week impression / surprise | Founder voice — "I installed Memee 4 days ago, here's what I didn't expect." | Soft signal; DO NOT fake testimonial |
| **Day 5** | Response to smartest critic (4-tweet reply) | QT or pure response; chart + reproduce command | Surfaces diskuze; rewards engagement |
| **Day 6** | RT-with-comment — early user post (if exists) | Real signal only; skip if no signal | Compound trust |
| **Day 7** | Recap with numbers | "Day 1 to Day 7: N installs, M stars. Here's what mattered." | Sets up next iteration |

---

## ⭐ Failure modes — kill switch

### Hour 4 (10:00 PT)

| Metric (cumulative) | Action |
|---|---|
| Headline post < 500 impressions | **Re-anchor**: post token-math chart with `$3,911/agent/year` framing as fresh tweet. Do NOT delete original. |
| HN < 30 points | Comment-1 has been ignored. Edit comment-1 to add a chart; do NOT submit again. |
| HN > 100 points | Engage in HN top-3 threads with technical depth. Stay off X for next 2h. |

### Hour 24 (06:00 PT Day 2)

| Metric (cumulative) | Action |
|---|---|
| > 50 installs (PyPI clones) AND > 100 stars | **Green** — execute Day 2-7 plan as written |
| 20-50 installs, 30-100 stars | **Yellow** — Day 2 post lands as written; Day 3-4 may need rewrite based on what's actually getting traction |
| < 20 installs AND < 30 stars AND < 1k impressions | **Kill switch** — cancel Days 2-7 cadence, plan Day-60 re-attempt with revised hook |

### Hostile QT response rule

**Do not reply for 4 hours.** Algorithm penalty + zero conversion + energy opportunity cost. Set phone timer when posting headline. Reply only after 4h, with one chart and one number. Never argument.

---

## ⭐ Metric stack

### Day-1 thresholds

| metric | bad | good | great |
|---|---:|---:|---:|
| Impressions on headline | < 1k | 5k | 20k+ |
| Profile visits | < 200 | 800 | 3k+ |
| Link clicks (memee.eu) | < 50 | 200 | 1k+ |
| GitHub clones (proxy for install) | < 20 | 60 | 200+ |
| GitHub stars | < 30 | 100 | 500+ |
| HN points | < 30 | 80 | 300+ |

### Day-7 thresholds

| metric | bad | good | great |
|---|---:|---:|---:|
| PyPI weekly downloads | < 100 | 500 | 2k+ |
| Total GitHub stars | < 100 | 300 | 1k+ |
| X followers gained | < 50 | 200 | 800+ |
| External RT/QT (org accounts) | < 5 | 15 | 50+ |

### Day-30 rerun threshold

If Day-30 weekly active downloads < 100 OR active issues < 3 → rerun launch motion v Q3 with revised hook based on Day-1-to-30 telemetry.

---

## ⭐ The ONE thing the founder must NOT do

**Reply to the first hostile QT within 4 hours.**

Mechanika:
1. **Algorithm penalty** — X reduces reach for accounts that spawn flame threads in first hour.
2. **Zero conversion** — hostile audiences don't install; engaging only validates them.
3. **Energy opportunity cost** — those 30 minutes are better spent replying to friendly engagement that converts.

**Phone timer ritual**: set 4h timer when posting headline. Until it fires: HN comments, GitHub issues, friendly replies only. After: ONE reply with one chart + one number, never argument.

---

## ⭐ Pre-launch warm-up (T-7 → T-1)

Strategista navrhuje **7 dní warm-upu** aby účet nevypadal jako sleeper:

| T- | Action |
|---|---|
| T-7 | One technical observation post — neutral, deep, no Memee mention |
| T-6 | Reply to 3 quality dev-tool conversations |
| T-5 | One "I've been working on something" tease — soft, no link, no install |
| T-4 | Technical observation #2 |
| T-3 | One reply under @swyx / @simonw / similar (genuine, technical) |
| T-2 | "Tomorrow this lands" — single sentence, no detail |
| T-1 | Bio update + banner ready, no posts |

Goal: when headline fires T-0 06:00 PT, account looks like a person who's been here, not a marketing channel.

---

## ⭐ Pinned bio (≤160 chars)

Z copywritera:

```
Built Memee in Prague. Cross-model shared memory for AI agents. MIT,
$0 to start. The thing your CLAUDE.md wishes it was. memee.eu
```

(159 chars. "Built in Prague" je credibility marker. "$0 to start" je
trust signal. "The thing your CLAUDE.md wishes it was" je hook.)

---

## ⭐ DM hooks (3 angles, ≤30 words each)

Když někdo DM-ne "what is this":

1. **"You've been there"** — *"You know how every Claude session forgets what you taught it last week, and you keep re-typing the same retry rule into CLAUDE.md? Memee is what fixes that."*

2. **"Everyone gets it wrong"** — *"Every memory tool stores embeddings. None of them survives a model swap. Memee writes one canon every model reads. That's the whole pitch."*

3. **"Look at this number"** — *"My CLAUDE.md was 14k tokens. Median across 27 popular OSS repos: 2,160. Memee briefings: ~40 tokens. Same hit rate. One install command."*

---

## What to compile / build / generate before T-0

### T-7
- [ ] Decide headline (A vs B) — **A recommended**
- [ ] Generate creative asset using Prompt B (B → A → C fallback chain)
- [ ] Bump `pyproject.toml` 1.1.0 → 1.2.0
- [ ] Tag `v1.2.0` on main
- [ ] Update site eyebrow to v1.2.0
- [ ] Update README hero to match chosen headline
- [ ] Run `python -m tests.retrieval_eval --save v1_2_0_pin` — pin numbers for thread

### T-3
- [ ] Test `pipx install memee` on fresh macOS install
- [ ] Test `pipx install memee` on fresh Linux install  
- [ ] Confirm `memee setup` and `memee doctor` work first try
- [ ] Confirm `pip install memee[rerank]` downloads cleanly

### T-1
- [ ] Bio updated
- [ ] Banner / profile picture set
- [ ] Reply-warm 5 target accounts done
- [ ] HN account ready (≥3 month old, ≥1 karma)
- [ ] Reddit account ready
- [ ] All draft posts saved as drafts in X
- [ ] Phone timer app ready

### T-0 06:00 PT
- [ ] Final dry-run
- [ ] Fire headline post + creative asset
- [ ] Set phone timer 4h
- [ ] Execute schedule

---

## Source artifacts

- [`docs/launch-copy.md`](launch-copy.md) — všechny copy artifacts (194 řádků)
- [`docs/launch-strategy.md`](launch-strategy.md) — playbook (713 řádků)
- [`docs/launch-creative.md`](launch-creative.md) — visual concepts (346 řádků)
- [`docs/release-positioning.md`](release-positioning.md) — site/README diff
- [`docs/launch-posts.md`](launch-posts.md) — original draft material

---

Last updated: 2026-04-25
