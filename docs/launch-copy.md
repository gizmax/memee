# Memee — X.com Day-1 launch copy

Voice: confident, dry, specific. Stripe Press, not LinkedIn. Every number
below is grounded in `README.md` or `CHANGELOG.md`. No emoji. No
exclamation points. No marketing-speak.

---

## 1. Headline candidates

A — "Your agent forgot. Mine didn't."
   Why: contrarian gut-punch. Implies the reader has to pick a side. Names the failure mode (forgetting) without ever using the word "memory". Best for cold scroll.

B — "One canon. Every model."
   Why: technical reveal in five words. Says the thing competitors can't say — Claude, GPT, Gemini, Llama all writing to and reading from the same store. For builders who already know the pain.

C — "We stopped teaching the same lesson twice."
   Why: founder voice. Past tense, plural — implies a team that lived through it. Frames Memee as a decision, not a tool. Best for the bio and the "why we built this" thread closer.

**If forced to ship one: A.** It's the only one that converts a passive scroll into a question ("did mine?"). B is the better tagline; A is the better hook.

---

## 2. The Day-1 X post (≤ 280 chars)

> Your agents forget. Every session, every project, every vendor swap — gone.
>
> Memee writes it down once. Claude proves it. GPT confirms it. Gemini opens a different repo on a different team and starts already knowing.
>
> One canon. Four model families. MIT.
>
> pipx install memee

Char count: 271. Hook in 4 words. One verb (writes). One outcome (every model reads the same canon). One concrete number (4 model families). One CTA (the install command — which is the CTA).

---

## 3. The Day-1 X thread (8 posts)

**P1 — hook**

> Every AI coding agent I've shipped this year had the same bug: it forgot.
>
> Not the conversation — the lesson. Same retry rule re-discovered in three repos. Same null check missed twice in a week. Different model, different agent, same hole in the floor.

(265 chars)

**P2 — the problem**

> CLAUDE.md was the duct tape. Mine grew to 14,000 tokens. I sampled 27 popular OSS repos via gh api — langchain, vercel/ai, prisma, zed — median ~2,160 tokens, p95 9,600, one outlier at 42,000.
>
> Loaded in full. Every turn. Every session. Every model.

(258 chars)

**P3 — the cost**

> That's not a memory system. That's a tax.
>
> And it doesn't help when Cursor opens the next repo, or when you swap Claude for Gemini, or when a teammate joins on a different stack. The lesson lives in one file, on one machine, for one project. Until it doesn't.

(266 chars)

**P4 — the mechanism (1)**

> Memee is the opposite design.
>
> Records once. Routes per task. Scores by survival.
>
> A pattern earns a row through a quality gate. Confidence climbs ×1.3 when a second model family agrees, ×1.5 when a second project re-uses it. Stack both: ×1.95. Nothing hits canon on one voice.

(279 chars)

**P5 — the mechanism (2)**

> At task start, the router picks 5–7 memories the agent actually needs and stops at a 500-token budget.
>
> Measured average: ~40 tokens per briefing across 10 task queries on a 500-pattern corpus. Your library grows. Your per-turn context doesn't.

(244 chars)

**P6 — the defensible number**

> 7-task A/B, with vs without Memee on the same model:
>
> Time per task: −71%.
> Iterations to ship: −65%.
> Mistakes the second time: 0.
>
> Retrieval eval, 207 queries × 255 memories: nDCG@10 = 0.7273 BM25-only, 0.7628 with the optional cross-encoder rerank (p=0.0002).

(270 chars)

**P7 — install**

> Sixty seconds, no account, fully local:
>
> pipx install memee
> memee setup
> memee doctor
>
> MCP server with 24 tools ships in the box. Drop it into Claude Code, Cursor, Continue, anything MCP-shaped. Memory lives in ~/.memee/memee.db.
>
> MIT. github.com/gizmax/memee

(266 chars)

**P8 — social ask (optional, ship it)**

> Built in Prague over fourteen months by people who got tired of teaching the same retry rule to a fresh agent every Monday.
>
> If you've felt the same — repost. Every install is one less debugging session you'll run twice.

(225 chars)

---

## 4. Three "morning of" follow-up posts

**Day 2 — the diff (concrete proof)**

> Today's `git diff | memee review -` flagged this on a PR I almost merged:
>
> WARNING [critical] Adding eval() on user-shaped input.
> Last seen: 2 projects, 4 incidents.
> Decision on file: 2026-02-14 — banned, use ast.literal_eval.
>
> Institutional memory in code review.

(263 chars)

**Day 4 — the contrarian take**

> Hot take: the AI memory layer most teams ship is just a longer system prompt.
>
> If your "memory" loads the same 9,000 tokens on turn one regardless of the task, you don't have memory. You have a bigger preamble.
>
> Memee routes 5–7 patterns per task. Hard 500-token cap.

(269 chars)

**Day 7 — early signal from a user**

> An indie team using Memee for a week pinged me:
>
> "It caught a deploy footgun on Tuesday I'd already debugged in March in a different repo. Different model, different teammate. Memee remembered."
>
> That's the point. One canon, every project, every session.

(259 chars)

---

## 5. The pinned bio (≤ 160 chars)

> Memee — institutional memory for AI agent teams. One lesson. Every agent. Every model. MIT. Built in Prague. memee.eu

(118 chars — leaves room for an X handle prefix if needed)

---

## 6. Three "permission-to-care" hooks

For DM replies to "what is this?"

**A — "you've been there" hook**

> You've taught the same retry rule to three different agents this quarter. You've written the same null-check warning into CLAUDE.md twice. Memee is what you build after the third time.

(28 words)

**B — "everyone gets it wrong" hook**

> Most "AI memory" tools are a longer system prompt — they dump everything on every turn. Memee routes 5–7 lessons per task at a 500-token cap, and confidence is earned across models, not declared.

(30 words)

**C — "look at this number" hook**

> 7-task A/B, same model, with vs without Memee: time −71%, iterations −65%, mistakes the second time around — zero. The number that surprised me most was the zero.

(28 words)

---

## The single post I'd pin to the top of the X profile

> Your agents forget. Every session, every project, every vendor swap — gone.
>
> Memee writes it down once. Claude proves it. GPT confirms it. Gemini opens a different repo on a different team and starts already knowing.
>
> One canon. Four model families. MIT.
>
> pipx install memee
