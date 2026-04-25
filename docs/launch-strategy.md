# Memee — Day-1 Launch Strategy on X

> Operator's playbook for a cold launch on X with no budget and no
> influencer pre-seeding. Founder pulls the trigger this week.
> Audience: AI builders, Claude/Cursor power users, indie hackers,
> OSS LLM-tooling maintainers, dev-tool buyers (1–10 person teams).

Last updated: 2026-04-25.

---

## 1. The thesis of the swing

**We're betting that a senior engineer scrolling X at 09:00 PT on a
Tuesday will stop for *one specific number stated honestly* — not for
a manifesto, not for a demo gif, not for a screenshot. The number is
**`~2,160 → ~40 tokens per turn`** with a labelled methodology and a
one-line install. Memee is provably useful before the reader has
opened the repo, because the math is on the post. We're *not* betting
on virality, on an influencer RT, on Show HN tier-1 placement, or on a
"founder story" thread — those are upside, not the plan. The plan is:
land one technical post that survives a quoted reply by someone who
knows IR, get 30–50 installs from people who would have built this
themselves, and let the second wave compound the signal as those
installs turn into anecdote-shaped quote-tweets over Days 2–7.**

What carries the swing if everything else fails: **the install command
in the first post**. Not in a thread reply. In the post.

What kills the swing: framing this as a "memory product" instead of a
**"96% fewer tokens per agent turn, here's how"** product. The
reader's first thought has to be tokens, not memory.

---

## 2. Day-1 timing + sequencing

### Pre-launch warm-up (T-7 days → T-0)

The X algorithm punishes accounts that post once after silence. Seven
days of warm-up is the minimum required to signal "real account" to
the ranker.

**T-7 days:**
- Profile picture: clean headshot, not a logo. Bio: one line, no
  emoji, no pronouns-list, no rocket. Format: `Building Memee —
  cross-model memory for AI agents. memee.eu`
- Banner: the canonical token-math chart from `docs/benchmarks.md`,
  exported at 1500×500. Single number, one footnote. No screenshot of
  the dashboard. No collage.
- Pinned post: cleared. Will be replaced on T-0.

**T-7 → T-1, post cadence:**
Post once per day, technical, quote-able, no hint that a launch is
coming. The point is to establish that this account writes things
worth reading *before* it asks for anything. Topics, in rough order:

1. T-7: a screenshot of the OrgMemEval scoreboard with one line of
   commentary on why competitor scores cluster at <4. No CTA.
2. T-6: a reply-quote of a popular AI-engineering post, adding a
   technical correction or extension. ~150 chars, no link.
3. T-5: a subtweet of the "agent memory" hype — "every 'agent memory'
   demo I've tried hits ≤30% recall on a 200-query harness once you
   leave the demo dataset. Receipts when I have time." Generates
   curiosity without committing.
4. T-4: a 4-tweet thread on confidence scoring math (cross-model
   ×1.95 multiplier). End: "writing this up properly soon."
5. T-3: link to one open issue you fixed in a popular OSS repo this
   week (genuine activity — not fabricated). Establishes you ship.
6. T-2: a reply to a question on a live HN thread about agent memory.
   Polite, technical, link-free. Profile views compound.
7. T-1: the **token-math screenshot** from the launch post —
   *without the install command*. Caption: "going to write up the
   methodology tomorrow." This is the seed. If it lands well at all,
   T-0 is downhill.

**T-1 evening (CET):** Stage everything. Pre-write Day-1 posts in
drafts. Run `pipx install memee` from a clean macOS user, a clean
Ubuntu container, and a Windows WSL — verify each succeeds in <60s.
Capture install screenshots. If any platform fails, **delay the
launch.** The first hostile reply will be "doesn't install."

### Day-1 schedule (Tuesday or Wednesday recommended, never Mon/Fri)

All times in **PT** because the X audience for dev tools is US-
weighted. CET is the founder's local; both are listed.

| PT | CET | Action |
|---:|---:|---|
| 05:30 | 14:30 | Final dry-run: `pipx install memee`, `memee setup`, `memee brief --task "write tests"`. Screenshot the briefing output. |
| 05:55 | 14:55 | Update pinned bio link to `memee.eu`. Confirm GitHub repo is public, README final, releases page has `v1.2.0`. |
| **06:00** | **15:00** | **Headline post fires.** Single image (the token-math chart), four lines of copy, install command in line 4. No thread yet. |
| 06:05 | 15:05 | Reply to own headline post with **Thread 1 (technical)** from `docs/launch-posts.md`. 7 tweets. Deliberately *not* the headline, because the headline must survive standalone. |
| 06:30 | 15:30 | Submit to Hacker News. Title: *Show HN: Memee – cross-model shared memory for AI agent teams (MIT)*. Do not reply on HN for the first 30 minutes — let it accrue organically. |
| 06:35 | 15:35 | Quote-tweet your own headline with: "HN thread is up if you'd rather argue there: [link]." This funnels HN-curious traffic and signals legitimacy. |
| 07:00 | 16:00 | Post in `r/LocalLLaMA` using copy from `launch-posts.md` C.1. Submit only — no comment yet. |
| 07:15 | 16:15 | Reply to first three substantive HN comments with technical depth. Never defensive, always with a number. |
| 08:00 | 17:00 | Post in `r/programming` (variant C.2). Lobsters submission too if you have an invite — skip if you don't. |
| 09:00 | 18:00 | Post **Thread 2 (economics)** as a fresh top-level post — *not* threaded under headline. Frames the same value for non-IR readers. |
| 10:30 | 19:30 | "First-hour numbers" follow-up: a bare post — "First three hours: N installs, M stars, K HN points. Highest-bandwidth feedback so far: [paraphrased criticism]." Honesty signal. |
| 12:00 | 21:00 | Post one screenshot of the *most surprising* HN comment with your reply. Surfaces the discussion to people who weren't on HN. |
| 14:00 | 23:00 | EU/CET dinner-time post: a single technical detail nobody noticed yet (e.g. "the dedup uses MinHash LSH on quality-gate ingest, 6.7× warm — turns out everyone's vector store stores the same lesson 3 times"). Stand-alone, not a thread. |
| 16:00 | 01:00+1 | Founder logs off. Schedules a single post for 06:00 PT Day-2. **Do not reply to anything for 12 hours.** Algorithm rewards the gap; founder needs sleep; trolls outrun themselves. |

**Pinned post for the next 7 days:** the headline post (not the
thread). The thread is reachable from the headline; the headline is
the asset.

**Profile picture / banner change on Day 1:** *don't*. Account looks
more authentic when nothing changes the day of the launch. If the
banner is wrong on T-7, you missed the deadline — ship anyway.

---

## 3. Hook engineering — what makes the first 7 words work

The headline post. This is the only post that matters.

**Recommended draft (272 chars including image alt):**

```
~2,160 tokens of CLAUDE.md per turn.
~40 tokens with Memee.

Same hit@1. Cross-model. MIT.

I measured it across 27 popular repos (langchain, vercel/ai, prisma,
zed, openai/codex…) and a 207-query eval. Methodology in repo.

  pipx install memee
```

The first 7 words are: **`~2,160 tokens of CLAUDE.md per turn`**.

That's the intellectual provocation. It is a *concrete number a senior
engineer can mentally check against their own setup right now*. They
will think one of the five things below in the first second.

### The 5 thought patterns we want firing

1. **"Wait — is that actually median?"** — Engineer mentally measures
   their own `CLAUDE.md`. Most are between 1.5k and 5k tokens. The
   number lands as plausible, not absurd. **If we said `~14,550` (the
   competitor-style hype number) instead, the reader rejects the
   post.** The honest median *is* the hook.
2. **"I've felt that frustration."** — Every Claude Code / Cursor
   power user has watched their `CLAUDE.md` grow past comfort and
   wondered if the agent is actually reading all of it. The post
   names a private pain in three numbers. They feel seen, then
   skeptical, then curious — that's the conversion sequence.
3. **"OK this person knows what they're doing."** — `hit@1`,
   `cross-model`, `27 repos`, `207-query eval` are four IR-literate
   tokens. The reader subconsciously rules out "vibes startup" and
   moves the post into the "might be real" bucket. We never have to
   *say* "we're technical."
4. **"`pipx install` not `pip install`?"** — Tiny detail. Means the
   author has shipped CLI tools before and knows about Python's
   site-packages problem. Trust signal worth more than any logo.
5. **"`gh api` to sample 27 repos — I could replicate this in 20
   minutes."** — The reader doesn't actually replicate. But the
   *feeling that they could* is what makes them quote-tweet with
   "interesting methodology." Replicability theatre is the cheapest
   form of authority on technical X.

### Anti-patterns we are explicitly avoiding

- **No "your agents forget"** in the first post. That's a tagline,
  and senior engineers smell taglines. Ship the tagline in tweet 5
  of the thread, not tweet 1.
- **No "we're launching today"**, no "after a year of work", no
  "excited to share". Excitement is for the people who weren't
  invited. Senior engineers were never invited; they don't need to
  be told.
- **No emoji.** Not even one. The `docs/launch-posts.md` editing
  notes correctly flag this; do not relax it under deadline pressure.
- **No GIF, no video.** A still chart with the methodology footnote
  reads as "scientist." A demo GIF reads as "marketer."

---

## 4. The amplification path (no budget)

With zero ad spend and no warm influencer relationships, 10k
impressions on Day 1 is achievable but *not guaranteed*. The path:

### A. Reply hooks — comment under these accounts in the 24h *before* T-0

Pick comments that are technical, additive, and don't mention Memee.
The goal is profile-page traffic, not a planted ad. Five accounts
whose engineering audience overlaps cleanly with Memee's:

1. **@swyx** (Shawn Wang) — posts AI-engineering threads daily, has
   the right reader. Comment under a thread about agent infra; share
   a measurement, no link. He often replies; if he does, that's a
   profile-traffic event.
2. **@simonw** (Simon Willison) — datasette / LLM CLI maintainer,
   audience overlaps perfectly with `pipx install memee`. Reply only
   under his "weeknotes" or `llm` tool posts; never under his
   personal stuff.
3. **@hwchase17** (Harrison Chase, LangChain) — pragmatic about agent
   memory limits; if Memee comes up, it'll be on his timeline first
   or never. Comment under a LangChain release post, not a hot take.
4. **@karpathy** (Andrej Karpathy) — the autoresearch loop in Memee
   *literally* implements his recipe. If a karpathy post about
   "compounding gains" or "metric+loop" appears in T-7..T-1, that's
   the natural place to leave one comment with one number. He almost
   never replies; that's fine — it's the followers, not him.
5. **@dzhng** / **@nutlope** / any AI Hackers / a16z infra-level
   builder. Pick whichever has posted in the last 48h about agent
   memory or context windows. Reply with a specific anti-pattern
   you've seen and quietly link memee.eu only if directly asked.

**Rule:** never lead with a Memee link. The link is in the bio. Profile
views are the conversion event in the warm-up phase.

### B. Link drops — where, when, what

| Channel | When | Why | Risk |
|---|---|---|---|
| **Hacker News** | 06:30 PT Day-1 (08:30 ET — this is when HN front page gets seeded for the day) | Tier-1 install driver if it lands. 30–80 installs from a top-10 placement, ~300 from front page. | Title and timing only matter for the first 90 min. After that, vote velocity decides. |
| **r/LocalLLaMA** | 07:00 PT Day-1 | Self-host audience, multi-model angle wins. 50–200 stars if it lands. | Mods are strict on self-promotion — body must be technical, no marketing language. |
| **r/programming** | 08:00 PT Day-1 | Broader reach, looser audience. | Likely to be downvoted by flaming "another AI tool" reply. Token-math angle is the only defense. |
| **Lobsters** | 08:30 PT Day-1 *only if you have an invite* | Small but high-signal. One front-page Lobsters post = ~20 quality installs. | No invite, no post — submitting via shared link is forbidden by culture. |
| **HN /show** | Same submission as above; HN auto-tags | 24h second-chance pool if first submission falls flat | None — same submission. |
| **Discord: LangChain, MCP, swyx's `latent.space`, Claude Devs** | 09:00 PT Day-1, in `#showcase` channels only | Where current Claude Code / Cursor power users *actually* hang out. 5–10 installs each, but the right 5–10. | Posting in `#general` is a permaban offence in most. Read the rules first. |
| **GitHub trending nudge** | Pre-prime by sending the repo URL to ~5 close friends Day-0 morning. Each star within an hour materially helps trending. | If you hit GitHub Trending Python on Day 2, that's another 100+ stars compounding on its own. | If you ask 5 friends and only 1 stars, the algorithm punishes the "low conversion velocity". Pick people who will actually star within an hour. |

### C. The "interesting reply" play

When a known account (call them @REPUTED) replies with skepticism, the
correct comeback is **a screenshot of the eval, not an argument**.
Specifically:

- @REPUTED replies: "Looks like another vector DB wrapper. What's the
  retrieval like?"
- Wrong response: "It's not a wrapper, it's a memory layer with
  cross-model scoring..." — sounds defensive, reads as marketing.
- Right response: paste the per-cluster nDCG screenshot from
  `CHANGELOG.md` lines 27-31 (cross-encoder rerank: onboarding +0.11,
  diff_review +0.06). Caption: "207-q × 255-m harness, paired
  permutation test, repro: `python -m tests.retrieval_eval`. Not a
  wrapper. Open to being told it's still wrong."

That last sentence — "open to being told it's still wrong" — is the
load-bearing element. Senior engineers respect a public concession
of falsifiability. They quote-tweet that. They don't quote-tweet
"actually it IS a memory layer."

If @swyx or similar high-reputation IR-literate person replies (real
unknown — they may not), the same script applies. The comeback is
*always* a chart and a reproduce command, never an argument in prose.

### D. The Show HN angle

**Title:** `Show HN: Memee – cross-model shared memory for AI agent teams (MIT)`

(72 chars. Avoid "open-source" — redundant with MIT. Avoid
"institutional memory" — too abstract for HN scanners. "Cross-model"
is the differentiator most likely to draw the IR crowd.)

**Time:** 06:30 PT (09:30 ET) Tuesday. The HN front-page seeding
window is 08:00–10:00 ET; 30 minutes ahead of the seed lets the post
accumulate the first 5 votes that decide everything.

**Comment-1-by-author** (post immediately after submission, *before*
any other comment lands):

```
Author here. Two things worth flagging up front:

1. The token-savings numbers in the README (96 % at the median,
   ROI ~10× on the 7-task A/B) are from internal simulations against
   `gh api`-sampled CLAUDE.md / AGENTS.md from 27 popular OSS repos.
   They are not third-party benchmarks. I would much rather someone
   replicate them and tell me they're wrong than nobody check. The
   sampling list and per-repo file sizes are in docs/benchmarks.md.

2. The retrieval evaluation IS independent-ish: 207 queries × 255
   memories with 7 difficulty clusters and a paired 10k-iter
   permutation test for the ship rule. nDCG@10 = 0.73 default,
   0.76 with the optional cross-encoder rerank. Reproduce with
   `python -m tests.retrieval_eval`.

Happy to answer implementation questions in the thread. The least
interesting parts (the marketing site) are the most polished; the
most interesting parts (the confidence-scoring math, the dream-mode
consolidation) are the least polished. PRs welcome on either end.
```

This comment does three things at once: (1) inoculates against the
"how do I trust your numbers" complaint, which is the #1 HN failure
mode for AI tools; (2) hands the audience a falsifiability hook;
(3) the "least polished/most polished" line is *human* — HN rewards
human voice over corporate voice every single time.

### E. Product Hunt — skip

Defend: Product Hunt's audience is no-code makers and PM Twitter,
not the engineers we need. A PH launch costs founder energy on Day 1
that should be spent replying to HN. The audience that converts
(self-hosters, OSS maintainers) finds Memee via HN and X, never via
PH. We can launch on PH on **Day 30** as a "milestone" post if we
want a second wave; doing it Day 1 dilutes attention.

Counter-argument: PH launches sometimes cross-pollinate to LinkedIn,
where dev-tool *buyers* (the $49/month memee-team buyer) live. True,
but LinkedIn is Day 1 anyway via the prepared post in
`launch-posts.md` D — which is a better LinkedIn surface than a PH
embed. Skip.

---

## 5. The second wave (Days 2–7)

The X algorithm hands out a 24h "honeymoon" of slightly elevated
reach to a post that performed above an account's baseline on Day 1.
After that, every post is back to baseline unless engagement
compounds. So Days 2–7 are about turning Day-1 launch traffic into
*evidence* that other people are using Memee, which is the only form
of social proof that survives a skeptical reading.

### Day 2 — Technical post, what shipped

**Topic:** the cross-encoder reranker (the single most measurable
v1.2.0 win — see `docs/release-positioning.md` and `CHANGELOG.md`
lines 38-44).

**Structural template:**
```
[number].
[mechanism in 1-2 sentences].
[reproduce command].
[honest caveat].
```

**Example post (267 chars):**
```
+0.0355 nDCG@10. p=0.0002.

That's what `pip install memee[rerank]` does to the 207-query
retrieval eval — onboarding queries jump +0.11, diff-review +0.06.

  python -m tests.retrieval_eval --vectors

Latency: +40ms p50. Optional dep, default off. Telemetry to
auto-promote pending.
```

### Day 3 — Contrarian take or honest negative

**Topic:** the *honest negative* from R14, that field-aware BM25
weights didn't ship (CHANGELOG lines 65-71). Engineers love to read
about what didn't work, *especially* when shipped from a launching
account.

**Structural template:**
```
[hypothesis we tested].
[result that disproved it].
[what we shipped instead].
```

**Example (276 chars):**
```
Spent two days swearing field-weighted BM25 (title×8, content×1,
tags×3) would lift our retrieval eval by +0.01 nDCG@10.

Measured: +0.0058 at p=0.32. Below ship rule. *Every* tuple
regressed lexical_gap_hard by ≥0.03.

R11 hypothesis dead. Cross-encoder rerank carried the round
instead.
```

This kind of post is *high* virality on technical X because it's
unfakeable — only people who actually ran the experiment talk this
way. Quote-tweet rate is 3-5× a "we shipped" post.

### Day 4 — User testimonial play

We have no users yet. Day 4 is at most ~72h since Day 1. The honest
play: *the founder's own first-week impression, framed as a user
post*.

**Structural template:**
```
[surprising finding from real use].
[before/after].
[footnote].
```

**Example (270 chars):**
```
Three days in. Memee's review hook flagged a `requests.get(url)`
in a PR with:

  "WARNING high: timeout missing. Same lesson invalidated 14× in
   3 projects. Use timeout=10."

That lesson was *my own*, recorded in 2024 on a different repo.
Memee just retaught it to me. That's the entire product.
```

If a *real* user posts a similar finding by Day 4 — quote-tweet it
with one sentence: "this is the case study I wanted to write,
written better." Don't add commentary. Let their words carry.

### Day 5 — Response post

Pick the **single best HN or X comment** from Days 1-4 — preferably
a critical one with a real argument — and post a long-form reply that
treats the criticism as legitimate.

**Structural template:**
```
[Quote of criticism, paraphrased fairly].
[What we agree with].
[What we disagree with, with evidence].
[Either: shipped fix, or: known limitation we won't fix].
```

**Example trigger criticism:** "Cross-model confidence is just
n-of-2 self-agreement. Two LLMs agreeing on a wrong fact doesn't
make it right."

**Example post (4-tweet thread):**
```
1/ Best criticism of Memee from the launch: "cross-model confidence
   is just n-of-2 self-agreement; two wrong LLMs don't make a
   correct fact."

   This is partially right and worth a careful answer.

2/ Where they're right: same training data, same RLHF lineage, same
   pretraining corpus → confirmation between Claude and GPT is *not*
   independent evidence in the statistical sense.

   We acknowledge this. The ×1.95 multiplier is calibrated to
   "more than same-model same-project," not "two oracle witnesses."

3/ Where they're wrong: validation in Memee isn't "did Claude and
   GPT both *say* this", it's "did the lesson hold across two
   *different runs* on two *different repos* under two *different
   models*."

   That's an empirical agreement on outcome, not a vote.

4/ The real defense though: confidence in Memee is a *prior for the
   router*, not a truth claim. The router prefers high-confidence
   memories, but the agent still has to make the call.

   Open issue tracking calibration: [link].
```

A four-tweet response to a smart critic, treating their argument
seriously, is the single strongest signal a launching dev tool can
emit. Tier-1 X engineers reshare these reliably.

### Day 6 — RT-with-comment play

By Day 6, *somebody* on X has posted a tangentially-relevant take
about agent memory or context engineering. Quote-tweet them with one
sentence + chart. The chart is always the same: cross-cluster nDCG
from CHANGELOG. The sentence varies.

**Structural template:**
```
[1-sentence agreement or extension].
[chart].
```

**Example (188 chars + image):**
```
Strong agreement. The router we wrote for Memee picks 5-7 memories
per turn from a corpus of 500+. The 500-token cap is the only thing
that keeps `CLAUDE.md` bounded.

[chart: routed-vs-CLAUDE.md token distribution]
```

### Day 7 — Recap with numbers

The honest week-1 recap is the most-shared post a launching dev tool
will ever write *if and only if* it includes a real failure number.

**Structural template:**
```
Week 1 in numbers.

Installs: N
Stars: M
Issues opened: K
Issues closed: K-J
Most-painful bug found by users: [one specific thing]
What I'd do differently: [one specific thing]

Repo: [link]
```

**Example (270 chars):**
```
Week 1 of Memee in public:

  Installs: 412 (PyPI)
  Stars: 287
  Issues opened: 23
  Issues closed: 18
  Embarrassing bug shipped: tokenizer crash on Cyrillic queries
  What I'd do differently: ship the 207-q eval *in the README hero*

Thanks for breaking my software.

  github.com/gizmax/memee
```

Cross-post Day-7 to LinkedIn (different audience, longer copy from
`launch-posts.md` E recap). Numbers in LinkedIn version skew toward
business — installs by company size if we have telemetry, ROI cited
from real usage if any user shared it.

---

## 6. Failure modes and the kill switch

A launch can fail in three windows. Different metrics matter in each.

### Hour-4 check (10:00 PT Day 1)

By hour 4, the headline post has either crossed 2k impressions or it
hasn't. The Twitter algorithm makes its first re-rank decision at
hour 1; by hour 4, the trajectory is set unless something
intervenes.

| Symptom | Diagnostic | Action |
|---|---|---|
| Headline post < 500 impressions, < 5 likes at hour 1 | Hook didn't hit. Could be timing, could be copy. | Post a *fresh* take on the same chart at 10:30 PT with a different opening line — try **"$3,911 / agent / year"** instead of token math. *Do not delete the original*; deletion signals failure to the algorithm. |
| Headline post 500–2000 impressions but < 1% engagement rate | Reach without resonance. The scroll is not stopping. | At hour 4, post the **most replied-to HN comment** as a screenshot with one-line commentary. Re-anchors the conversation. |
| Headline > 2000 impressions, normal engagement | On track. | Stay the course. Do not improvise. |
| HN post < 5 points after 30 min | Falling off /new. ~80% chance dead. | At hour 1, post the **second-chance pool comment** ("Author — happy to take questions on the multi-model confidence math, the cross-encoder rerank, or how the dream-mode consolidator handles contradictions") as a self-reply. HN rewards substantive author engagement. |
| HN post < 10 points at hour 4 | Effectively dead. | Don't post a second submission. HN flags duplicates. Wait 14 days, resubmit with a different angle ("how we measured 96% token reduction…"). |

### Hour-24 check (06:00 PT Day 2)

| Symptom | Action |
|---|---|
| < 30 PyPI installs in 24h | Distribution failed, not the product. Skip Day-2 technical post. Instead: post a 4-tweet thread *explaining what people are missing*. Specifically: "Here's what 96% fewer tokens *means at your stack*: [worked example using their actual CLAUDE.md size]." |
| 30–100 installs | Normal cold launch. Continue Day-2 plan. |
| > 100 installs | Above plan. The Day-7 recap will write itself. Don't accelerate; Days 2-7 cadence is set. |
| GitHub stars: < 50 | The repo readme is doing the conversion work below threshold. Pin the v1.2.0 changelog highlights to the readme top. |
| GitHub stars: > 200 | Trending Python is plausible by Day 3. Don't ask anyone to star — that backfires. Focus on issues replies. |

### The hostile-QT response rule

A hostile quote-tweet (>5k impressions, calling Memee "AI slop" or
"another vector DB wrapper") will land between hour 6 and hour 24.
Not a probability — a near-certainty.

**Default: do not reply for 4 hours.** Specifically:

- If the QT is *factually wrong* (e.g., "doesn't work with Claude"),
  reply once with the install command and a screenshot of `memee
  doctor`. No argument.
- If the QT is *opinionated but technical* ("vector search is solved,
  this is reinventing it"), reply once with the per-cluster nDCG
  chart. One sentence: "Reasonable to think so. Here's where BM25
  alone falls down." Move on.
- If the QT is *personal or trolling*, do not reply. Block if it
  escalates. Engagement is what they want; non-engagement starves it.

**Hard rule:** the founder writes one reply per hostile QT, then
closes the tab. The thread will burn itself out in 12 hours.
Replying twice is what makes it burn for 48.

### Kill switch

If at hour 24 we have **< 20 installs and < 30 stars and the headline
post is under 1000 impressions**, the launch failed to land. The
correct response is *not* to repost or boost. The correct response
is:

1. Cancel the Day-2 through Day-7 schedule.
2. Pin a single quiet message: "Memee is live at memee.eu. Working on
   distribution; if this is interesting, the install command is
   `pipx install memee`. Feedback welcome via GitHub issues."
3. Spend the next 30 days writing one technical post per week — the
   Day 2-3-5 templates are excellent for that — with no launch
   framing.
4. Re-attempt a launch on **Day 60** when v1.3 ships, with the lesson
   "Day 1 was wasted because we led with memory framing, not token
   framing" baked in.

A failed launch is not a failed product. The kill switch protects
energy for the second attempt.

---

## 7. The metric stack

### Day-1 metrics (collected at 06:00 PT Day-2)

| Metric | "Bad launch" | "Good launch" | "Great launch" |
|---|---:|---:|---:|
| Headline post impressions | < 1k | 2k–10k | > 25k |
| Headline post engagement rate | < 1% | 2–5% | > 5% |
| Profile visits (X analytics) | < 200 | 500–2000 | > 5000 |
| Link clicks to memee.eu | < 50 | 200–800 | > 1500 |
| `pipx install memee` events (PyPI BigQuery) | < 20 | 50–200 | > 400 |
| HN points at hour 4 | < 10 | 25–80 | > 150 (front page) |
| GitHub stars | < 30 | 100–300 | > 500 |
| HN comments | < 5 | 15–40 | > 60 |
| New X followers | < 30 | 100–300 | > 600 |

PyPI download events have a 48-hour lag in the official BigQuery
dataset. For Day-1, use **GitHub clone count** (Repo > Insights >
Traffic) and **GitHub star velocity** (stars per hour) as proxies.

### Day-7 metrics

| Metric | Bad | Good | Great |
|---:|---:|---:|---:|
| GitHub stars | < 100 | 300–800 | > 1500 |
| PyPI weekly downloads | < 100 | 300–1000 | > 2000 |
| X followers | < 100 | 400–1200 | > 2500 |
| GitHub issues opened by *non-author* | 0 | 5–15 | > 25 |
| External blog posts mentioning Memee | 0 | 1–3 | > 5 |
| `memee.eu` unique visitors | < 1k | 3k–10k | > 25k |

### Day-30 metrics

The quiet-but-real ones. Day-30 separates "we had a launch" from "we
have a product."

| Metric | Bad | Good | Great |
|---:|---:|---:|---:|
| Weekly PyPI downloads (steady-state) | < 50 | 200–800 | > 2000 |
| GitHub stars added between Day 7 and Day 30 | < 50 | 150–500 | > 1500 |
| Active X conversation about Memee in last 7 days (search "memee" / "memee.eu") | 0–2 mentions/week | 5–20 mentions/week | > 50 mentions/week |
| Inbound `info@memee.eu` Team-tier inquiries | 0 | 3–8 | > 15 |
| Independent contributor PR merged | 0 | 1–3 | > 5 |
| Repos with `memee` in their `requirements.txt` (GitHub code search) | < 5 | 20–80 | > 200 |

**Rerun threshold:** if Day-30 weekly PyPI downloads are below 50
and inbound Team inquiries are zero, the product/market is the
problem, not the launch. Time to revisit the messaging hypothesis,
not relaunch.

---

## 8. The one thing the founder must NOT do

**Do not reply to the first hostile quote-tweet for 4 hours.**

Specifically: at some point on Day 1, between hour 4 and hour 12,
someone with > 50k followers will quote-tweet the headline post with
a dismissive one-liner. Most likely versions:

- "another vector DB wrapper"
- "your benchmarks are simulated, this is marketing"
- "AI memory is a solved problem, see [their preferred tool]"
- "MIT but the real product is paywalled, classic"

The founder's instinct will be to reply within minutes with a
careful, evidence-rich correction. **This is the single most
expensive action available on Day 1.** Specifically:

1. The QT author's followers are already in their replies. The
   founder's correction lands in a hostile audience that has already
   read the QT and decided. Conversion rate from that audience: ~0.
2. The X algorithm reads "founder replies fast to hostile QT" as
   "founder is anxious, this is a small account, throttle." Reach
   to *friendly* readers drops in the next hour.
3. The founder has just spent 30 minutes of high-cognitive-load
   energy on a thread that converts nobody, when that energy should
   be on the HN comments where it converts everyone.

The 4-hour wait does three things instead:

1. The QT thread burns itself out on its own audience. By the time
   the founder replies, the heat is gone, and the reply lands as
   measured rather than reactive.
2. The friendly traffic to the headline post compounds for 4 more
   hours uninterrupted.
3. The eventual reply is *one* reply with *one* chart, which is far
   more re-shareable than a defensive thread.

**Set a phone timer before posting the headline.** When the timer
fires, the founder has permission to reply. Until then: HN comments,
GitHub issues, replies to *friendly* engagement only.

---

## The one X post that, if it's the only thing the founder remembers, would carry the launch

```
~2,160 tokens of CLAUDE.md per turn.
~40 tokens with Memee.

Same hit@1. Cross-model. MIT.

Methodology in repo (gh-api'd 27 OSS repos, 207-query eval).

  pipx install memee
```

Six lines. One number. One install command. One trust signal
(MIT). One falsifiability hook (methodology in repo). Zero
adjectives.

If the founder posts only this and replies only to people who
engage in good faith, the launch lands.

Everything else in this document is leverage on top of that one
post — but that post is the swing.
