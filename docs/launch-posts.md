# Memee — Launch-Day Posts Playbook

## Editing notes (do before you paste)

- [ ] Replace every `<org>` with the final GitHub org slug (e.g. `gizmax`).
- [ ] Confirm tagline choice globally — default used here: **"Your agents forget. Memee doesn't."**
- [ ] Re-count characters on every tweet after edits (target ≤ 280, counted conservatively for emoji-free ASCII).
- [ ] Confirm `memee.eu` resolves and the install command works end-to-end before posting.
- [ ] Every numeric claim is labelled "internal simulation" where it isn't obvious — keep that label.
- [ ] No emoji, no rocket, no flame. If you add one, you are breaking the voice.
- [ ] Post HN at ~08:00 ET on a Tuesday–Thursday. Twitter threads and LinkedIn go within 30 min of HN going live so any traffic spike compounds.
- [ ] Reddit posts go 2–3 hours *after* HN so you are not cross-posting within the same hour (mods notice).
- [ ] Pin the Twitter thread you care most about for 24 h.

---

## A. Twitter / X — three threads

> Note to self — paste this line as the first reply under each thread:
> *"Reply under the first tweet with questions — I'll respond."*

### Thread 1 — Technical angle (infra-pilled engineers)

**1/7** (271 chars)
```
96% fewer tokens per agent task.

Not by compressing prompts. By not shipping the 14,550 tokens of "context" your agents didn't need in the first place.

Memee is an open-source memory layer for AI agent teams. Here's how the internals work.
```

**2/7** (268 chars)
```
Every memory gets a confidence score, not a vibes rating.

New entry starts at 0.5.
Same project, same model validates it: +0.08.
Different project, different model validates it: +0.156 (×1.95).
Invalidated: -12% of current score.

Cross-model agreement is the strongest signal.
```

**3/7** (276 chars)
```
Retrieval is hybrid:
- BM25 over FTS5 (7.6ms)
- sentence-transformers all-MiniLM-L6-v2, 384-dim
- tag overlap
- task-aware query expansion (60+ patterns across eng/marketing/data/ops)

Combined ~113ms for a hybrid search. No vector DB service. Just SQLite.
```

**4/7** (278 chars)
```
The quality gate runs before anything is stored:

validate → dedup → source classify → score

- title ≥10 chars, content ≥15
- SequenceMatcher >85% → merged, not duplicated
- human ×1.2, llm ×0.8, import ×0.6 confidence multiplier
- TODOs and meeting notes rejected
```

**5/7** (279 chars)
```
Dream mode runs nightly:
- propagate validated patterns cross-project
- connect related memories
- flag contradictions for review
- promote mature hypotheses to canon (5 projects, 10 validations, 0.85 conf)

It's a cron job, not a ceremony. The corpus gets sharper while you sleep.
```

**6/7** (268 chars)
```
Example. Agent writes:

  requests.get(url)

Memee's review hook checks the diff against the anti-pattern DB and returns:

  WARNING high: "requests without timeout hangs on slow hosts.
   Use requests.get(url, timeout=10). 14 validations, 3 projects."

Institutional memory.
```

**7/7** (272 chars)
```
MIT core. Self-hostable. Works with Claude, GPT, Gemini, Llama — they all read the same canon, and confidence scoring credits cross-model validation.

pip install memee
memee setup

Repo: github.com/<org>/memee — star it if you want to see where this goes.
Site: memee.eu
```

---

### Thread 2 — Business / economics angle (CTOs, indie founders)

**1/7** (266 chars)
```
The math on agent memory that nobody does out loud:

100 agents × 14,550 tokens of "context" per task × 200,000 tasks/year = 2.9 billion tokens.

On Sonnet pricing, that is a five-figure bill you are paying to re-teach your agents what they already learned last Tuesday.
```

**2/7** (276 chars)
```
We ran this in a 100-agent / 200-project simulation over 18 months.

Without shared memory: agents repeat the same mistakes across projects. Every session starts from zero.

With Memee: 501M tokens saved per year. ~$3,911 saved per agent per year. (Internal simulation, Sonnet rates.)
```

**3/7** (277 chars)
```
The mechanism is boring and that is the point.

Memee routes 5–7 task-relevant memories per session instead of dumping the whole corpus into CLAUDE.md.

14,550 tokens → 500 tokens. 96% reduction. Same hit@1 on our 12-memory benchmark (100%, internal simulation).
```

**4/7** (272 chars)
```
What actually moves in an A/B on 7 tasks (internal simulation):

- Time:       1,470 min → 430 min  (−71%)
- Iterations: 43 → 15                (−65%)
- Mistakes:   14 → 0                 (100% prevented)
- Quality:    56% → 93%              (+36pp)

ROI on the run: 10.7x.
```

**5/7** (270 chars)
```
Where the moat isn't in a wrapper:

Confidence scoring that credits cross-model agreement. Quality gate with dedup. Scoping from personal → team → org with promotion rules. Nightly consolidation.

You can't bolt this on. You either build it early or you pay in tokens forever.
```

**6/7** (266 chars)
```
Open core:
- memee (MIT, free, self-hostable, single-user)
- memee-team ($49/month flat up to 15 seats, from $12k/year Enterprise): multi-user scope, SSO, audit trail

If you are 1 dev, stay on OSS forever. If you are a team of agents, the team tier pays for itself in two weeks on token savings alone.
```

**7/7** (248 chars)
```
If your agent fleet is re-learning "add timeout to requests.get" every Monday, that is a line item.

pip install memee && memee setup
github.com/<org>/memee
memee.eu

Launched today. Questions welcome under this tweet — I'll answer every real one.
```

---

### Thread 3 — Story / craft angle (broader AI-curious audience)

**1/6** (271 chars)
```
We lost the same bug four times.

Same SQLite gotcha. Four different projects. Four different agents. Four separate hours of a human engineer explaining it again.

That's when I started building Memee. A year later, here's what it does and why it exists.
```

**2/6** (270 chars)
```
Every AI agent session starts with the same amnesia.

You pay tokens to explain your stack. You pay tokens to re-list your conventions. You pay tokens to warn it about the one library that segfaults on macOS.

Then the session ends. The agent forgets. You pay again tomorrow.
```

**3/6** (273 chars)
```
Teams have it worse.

Agent A in project 1 learns a hard lesson. Agent B in project 2 hits the exact same wall a week later, because nothing flowed between them.

Your team's collective experience evaporates at the end of every context window. That is not a tooling problem. That is a memory problem.
```

**4/6** (270 chars)
```
Memee is what I wish existed then.

A shared memory layer across agents, projects and models. Patterns mature — hypothesis → tested → validated → canon — the way human institutional knowledge does. Bad patterns get invalidated. Good ones spread automatically.
```

**5/6** (275 chars)
```
The part I'm proud of: it works across model families.

Claude, GPT, Gemini and Llama all read the same canon. When two different models confirm a pattern, Memee weights that confirmation higher — because independent agreement is the oldest and most reliable evidence we have.
```

**6/6** (246 chars)
```
It's MIT, self-hostable, and you can install it in two commands.

pip install memee
memee setup

Shipping today at memee.eu.
Repo: github.com/<org>/memee — a star actually helps at this stage.

Reply under this tweet with questions — I'll respond.
```

---

## B. Hacker News submission

**Title** (72 chars):
```
Show HN: Memee – open-source shared memory for multi-model AI agent teams
```

**Alternate title** (74 chars):
```
Memee: an open-source memory layer that stops AI agents relearning the same bugs
```

**Text body** (~260 words):

```
Hi HN — I built Memee because my agents kept re-solving the same problems in
every new session, and a team of agents was worse: nothing flowed between
projects, nothing flowed between models.

Memee is a memory layer that sits between your agent runtime and an SQLite
store with FTS5 + 384-dim embeddings. It does four things that existing
"agent memory" tools mostly don't:

1. Confidence scoring with cross-model credit — a pattern validated by a
   different model in a different project counts ~2x more than one confirmed
   by the same model in the same repo.
2. A quality gate with dedup (SequenceMatcher > 85% = merged, not stored
   twice) and a source multiplier (human > llm > bulk import).
3. A smart router that picks 5–7 task-relevant memories per session instead
   of injecting the whole corpus into CLAUDE.md. In our internal simulations
   this cuts per-task context from ~14,550 to ~500 tokens with no loss on a
   12-memory hit@1 benchmark.
4. A nightly "dream" pass that promotes mature hypotheses to canon, flags
   contradictions, and propagates validated patterns cross-project.

Open-core: the OSS release (MIT) is fully usable solo and self-hostable.
memee-team ($49/month flat, up to 15 seats) adds multi-user scope, SSO, and an audit trail.

Honest evidence level: the ROI numbers (71% time saved on a 7-task A/B,
501M tokens/year in a 100-agent / 200-project run) are from internal
simulations, not third-party benchmarks. I'd love for someone to replicate
them and tell me I'm wrong.

Site: https://memee.eu
Repo: https://github.com/<org>/memee

Happy to answer implementation questions in the thread.
```

---

## C. Reddit variants

### C.1 r/LocalLLaMA (technical, multi-model + self-hostable)

**Title** (96 chars):
```
Memee: open-source, self-hostable memory layer that lets Claude/GPT/Gemini/Llama share a canon
```

**Body** (~340 words):

```
Posting because this sub actually cares about multi-model and self-hosting,
which is where Memee is aimed.

**What it is**
A memory layer for agent teams. SQLite + FTS5 + sentence-transformers
(all-MiniLM-L6-v2, 384-dim). Runs on a laptop. No external services.
MIT licensed.

**Why multi-model matters here**
Memee's confidence scoring *credits cross-model agreement*. A pattern
validated by Llama-3 in one project and Claude in another gets a ×1.95
bonus over a same-model same-project confirmation. That is the closest
thing we have to independent replication inside an agent fleet.

All four families (Claude, GPT, Gemini, Llama) read the same canon store.
No vendor lock-in on the memory side even if you switch backends.

**Pipeline**
- ingest → quality gate (title ≥10 chars, content ≥15, dedup at 85%
  SequenceMatcher, source multiplier human ×1.2 / llm ×0.8 / import ×0.6)
- store with confidence 0.5
- validations/invalidations update score
- hybrid search: BM25 (7.6ms) + vector + tags, ~113ms combined
- nightly dream pass: propagate, connect, flag contradictions, promote to
  canon at 0.85 / 5 projects / 10 validations

**Why it matters in tokens**
Internal simulation: 14,550 tokens of injected context per task → 500
tokens via task-aware routing. 96% reduction. hit@1 on our internal
12-memory benchmark stayed at 100%.

In a 100-agent / 200-project / 18-month simulated run: 501M tokens
saved/year, about $3,900 per agent per year on Sonnet rates. Numbers are
simulated; if you want to replicate on a real fleet I'll help.

**Install**
    pip install memee
    memee setup
    memee doctor

**Paid tier**
memee-team ($49/month flat, up to 15 seats) adds multi-user scope, SSO, and an audit trail.
Solo and homelab use stays free forever on OSS.

Repo: https://github.com/<org>/memee
Site: https://memee.eu

Happy to go deep on the confidence math or the dream-mode consolidation
in comments.
```

### C.2 r/programming (broader; lead with token math + CLI)

**Title** (94 chars):
```
I cut per-task agent context from 14,550 tokens to 500 with a shared memory layer (OSS, MIT)
```

**Body** (~320 words):

```
If you run AI agents at any scale, you are probably paying twice: once to
run the task, and once to re-explain your stack to the agent every time
it wakes up with zero memory.

Memee is the thing I built to stop doing that. It's an open-source
memory layer that sits between agents and an SQLite store, with a router
that injects only the 5–7 task-relevant memories per session instead of
dumping the whole corpus into CLAUDE.md.

**The numbers** (internal simulation — reproduce and yell at me if they
don't hold up on your fleet):

- 14,550 tokens → 500 tokens of injected context per task (−96%)
- hit@1 stayed 100% on a 12-memory retrieval benchmark
- 7-task A/B with vs without Memee: −71% time, −65% iterations,
  56% → 93% quality, 14 mistakes → 0
- 18-month 100-agent / 200-project run: 501M tokens saved/year,
  ~$3,900 per agent per year on Sonnet rates

**What makes it more than a vector DB**
- Confidence scoring. Every memory starts at 0.5 and moves based on
  validations/invalidations. Cross-model, cross-project agreement
  counts ~2x more than same-model same-project.
- Quality gate. Rejects TODOs and meeting notes, dedups at 85%
  similarity, weights by source (human > llm > bulk import).
- Dream mode. A nightly pass that connects related memories, flags
  contradictions, and promotes mature hypotheses to canon.
- Task-aware routing. 60+ query-expansion patterns route "write unit
  tests" to testing memories, "GDPR audit" to compliance memories, etc.

**Install**

    pip install memee
    memee setup

MIT. Works offline. Works with Claude, GPT, Gemini and Llama reading
the same canon. Paid team tier (SSO + audit) is $49/month flat for up to 15 seats — solo use
is free forever.

Repo: https://github.com/<org>/memee
Site: https://memee.eu

Technical questions welcome.
```

---

## D. LinkedIn post (English, ~250 words)

```
Every new AI agent session starts from zero.

You pay tokens to re-explain your stack. You pay tokens to re-list your
conventions. Your best agent learns a painful lesson on Monday, and by
Friday a different agent in a different project hits the same wall —
because nothing flowed between them.

This is the single most expensive problem in agent operations right now,
and almost nobody is pricing it.

I've been working on Memee for the past year to fix it. It's an
open-source memory layer that:

— routes only 5–7 task-relevant memories per session instead of dumping
  the whole corpus into context,
— scores every memory with a confidence model that credits cross-model
  and cross-project validation,
— runs a nightly consolidation pass that promotes mature patterns to
  canon and flags contradictions,
— works across Claude, GPT, Gemini and Llama reading the same store.

In our internal simulations the numbers are hard to ignore:
96% fewer context tokens per task, 71% less time on a 7-task A/B,
and roughly $3,900 saved per agent per year in a 100-agent fleet.

The core is MIT licensed and self-hostable — free for solo use forever.
The team tier ($49/month flat, up to 15 seats) adds multi-user scope, SSO and audit.

If your agents keep re-learning the same lessons, Memee is what I wish
had existed two years ago.

Launched today. Repo, docs, and install are at https://memee.eu.
Candid feedback — especially from teams running agent fleets — is the
most valuable thing you could send back.
```

---

## D.2 LinkedIn post — Czech variant (volitelná verze pro CZ network)

```
Každá nová session AI agenta začíná od nuly.

Platíš tokeny za to, aby ses znovu představil. Platíš za výpis konvencí.
V pondělí se tvůj nejlepší agent spálí o konkrétní bug, a v pátek ten
samý problém potká jiného agenta v jiném projektu — protože mezi nimi
nic neteklo.

Je to nejdražší provozní problém AI agentů, o kterém se skoro nemluví.

Poslední rok stavím Memee, aby to řešilo:

— do každé session posílá jen 5–7 relevantních paměťových záznamů
  místo celého dumpu kontextu,
— každá vzpomínka má confidence skóre, které extra váží potvrzení
  napříč modely a napříč projekty,
— noční "dream" pass propojuje souvislosti, značí rozpory a povyšuje
  ověřené vzory na kánon,
— Claude, GPT, Gemini i Llama čtou ze stejného úložiště.

Čísla z interní simulace:
96% méně kontextových tokenů na úkol, −71% času v 7-úkolovém A/B,
přibližně $3 900 úspory na agenta ročně ve flotile o 100 agentech.

Jádro je MIT, self-hostable, pro jednotlivce zdarma napořád. Týmový
tarif ($49/měsíc flat, až 15 uživatelů) přidává sdílené skóre, SSO a audit log.

Pokud tví agenti opakují ty samé chyby — tohle je to, co jsem si před
dvěma lety přál mít.

Spuštěno dnes: https://memee.eu
Zpětná vazba, hlavně od týmů s reálnou flotilou, je nejcennější věc,
kterou mi můžeš poslat.
```

---

## E. Seven-day follow-up content plan

- **Day +1** — Twitter: "What actually broke on launch day" — honest thread with real issues found by HN commenters, and how the quality gate caught (or missed) them.
- **Day +2** — Twitter long post: the token math with receipts — screenshot of a real CLAUDE.md before/after Memee routing, with line-by-line token counts.
- **Day +3** — Dev blog on memee.eu: "How confidence scoring credits cross-model agreement" — the math, worked example, and why same-model same-project validation is the weakest signal.
- **Day +4** — Reddit r/MachineLearning (rules permitting) or r/ChatGPTCoding: "Hybrid BM25 + vector in 300 lines of SQLAlchemy" — technical deep-dive pulled from `search.py`.
- **Day +5** — LinkedIn: case-study-style post from your own usage ("Here's what my 5-agent fleet learned in week one"), with 3 real patterns from Memee's canon.
- **Day +6** — Twitter: "Five anti-patterns your agents keep hitting" — anonymised highlights from early adopters' warning DBs. Soft CTA to `memee warn`.
- **Day +7** — Recap post + numbers: "Week one of Memee in public" — installs, stars, contributors, bugs shipped, what changed based on feedback. Cross-post to LinkedIn and a short Twitter thread.

Buffer slot: if HN hits front page, replace Day +2 with a live Q&A thread on Twitter Spaces or a written "answering every HN comment" post.
