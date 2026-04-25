# Launch creative — Day 1 X post

> One asset. Zero stock. The thing that makes a stranger stop, click, install.

The mental image this product is, after reading the gamechangers and the
hero diagram: **a single index card, written once at 2 p.m. by one agent on
one repo, that turns up unread-but-known on every other agent's desk the
next morning.** Not a graph. Not a brain. Not a glowing network. A note
that travelled.

The anti-image we're fighting: the bloated `CLAUDE.md` — a 2,160-token
wall reloaded on every turn, eternally growing. The pro-image is the
opposite: small, bounded, signed, dated, scored. A canon entry, not a
dump.

---

## 1. The single creative concept

**A receipt that copied itself.**

A Polaroid-sized index card sits centred on a deep-graphite field — clean
typography, slightly off-axis, lit from above-left. It is unmistakably
*one specific lesson*: title, one-line content, a model badge ("Claude /
agent-07 · payments-prg"), a timestamp ("14:02"), a confidence score
(`×1.95`), and a rubber-stamped status (`CANON`) in cyan-mint. Behind it,
slightly out-of-focus, three near-identical cards are visible at staggered
depths — each stamped with a *different* model name (`GPT`, `Gemini`,
`Llama`) and a *different* repo name. Same pattern. Different reader.
Different time. Hot magenta hairline arrows trail from the front card to
the three behind it, like a passport stamp record.

It says: *one lesson, four readers, no re-teaching*. The eye reads the
front card first (the work), then notices the others (the propagation),
then the arrows (the mechanism). That's the whole product in one frame.

**Why a card and not a diagram:** the existing site hero already *is* the
diagram (source → core → receivers). Re-running that visual on launch is
self-referential. The card is the *artefact* the diagram produces — the
thing that exists on disk, the thing that gets re-read. Showing the
output is more confident than showing the system.

### ASCII layout (1:1, 1080×1080)

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│                                                              │
│                  ┌────────────────────────┐                  │
│                  │  pat-7f3a · CANON  ✓   │  ← front card   │
│                  │                        │     (sharp)     │
│                  │  retry with jitter     │                  │
│                  │  exp backoff, cap 30s, │                  │
│                  │  idempotent verbs only │                  │
│                  │                        │                  │
│                  │  Claude / agent-07     │                  │
│                  │  payments-prg · 14:02  │                  │
│                  │  ×1.95  ████████ 0.94  │                  │
│                  └────────────────────────┘                  │
│                       │  ╲                                   │
│                       │   ╲   ┌──────────────────────┐       │
│                       │    ╲  │ GPT / billing-brno   │ ← +1d │
│                       │     ╲ │ pat-7f3a            │       │
│                       │      ╲└──────────────────────┘       │
│                       │       ┌──────────────────────┐       │
│                       │ ────→ │ Gemini / payments-prg│ ← +2d │
│                       │       │ pat-7f3a            │       │
│                       │       └──────────────────────┘       │
│                       │       ┌──────────────────────┐       │
│                       └─────→ │ Llama / checkout-ba  │ ← +3d │
│                               │ pat-7f3a            │       │
│                               └──────────────────────┘       │
│                                                              │
│   memee  ·  one lesson. every agent. every team.             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

The three rear cards are visibly *softer* (motion blur, or depth-of-field
out-of-focus). Their timestamps are progressively further out. Same
pattern ID `pat-7f3a` on every card — that's the proof of identity.

---

## 2. Format decision: **still image, 1:1**

Not video. Not animated. Defended:

- **X auto-plays muted video, but devs scroll past flash.** A motion
  graphic of cards "flying" between rectangles reads as "marketing
  bullshit" to the exact audience we want (Linear / Vercel / Anthropic
  veterans). The Stripe Press / Anthropic PR house style is *static
  illustration*. Restraint is signal.
- **Still wins the 0.5s preview.** Video on X shows a frame-1 thumbnail
  that often looks like a bug. A still has *one* job: be that thumbnail.
- **A code screenshot is the safe boring play. A diagram says "this
  person took it seriously." A typeset artefact says "this person built
  the thing and respects you enough to show you the output."** That's
  the slot we want.
- **It works as a quote-tweet thumbnail.** When someone QTs the post,
  the same square reads correctly at 200×200 px — the front card stays
  legible because it's centred and large; the three rear cards reduce
  to silhouettes that still communicate "more of the same".
- **1:1 over 16:9** because X feed crops 16:9 to ~16:7 on mobile, and
  centred verticality is friendlier to the eye on a phone. Square also
  re-uses cleanly on Hacker News and Show HN if we cross-post.

If we *did* go animated, the only defensible motion is a 2-second loop:
front card lands, three rear cards fade in 200ms apart, magenta arrows
trace once, hold for 1.4s, hard cut and loop. Storyboard included
in §4 in case we need it for the second-week post.

---

## 3. Three image-generation prompts

All three target the same composition. They differ in art-direction
risk. Aspect ratio: `1:1`, `1080×1080`. Render at `2048×2048` and
downsample for crispness.

### A — Safe (Linear / Vercel-tier polish)

```
A single high-craft editorial illustration, 1:1 square, deep graphite
background (#0E1116). Centred composition: one rectangular index card,
roughly 60% of frame width, rendered as a clean physical artefact —
matte off-white paper (#EDEFF4), subtle paper grain, soft top-left key
light, faint drop shadow on graphite. The card carries crisp typography
in two faces: Inter Tight for the headline ("retry with jitter") set
~32 px equivalent, JetBrains Mono for metadata lines (model name,
repo, timestamp, confidence score "×1.95"). A small cyan-mint
(#00E5C7) rubber-stamped chip in the upper-right of the card reads
"CANON" with a checkmark. Behind the front card, three near-identical
cards are visible at receding depths, each progressively softer in
focus, each labeled with a different model name (GPT, Gemini, Llama)
and a different repo. Hot magenta (#FF4D8F) hairline arrows — 1 px,
no glow — connect the front card to the three behind it, like
passport-stamp trails. Bottom of the frame: a single line of small
type, "memee — one lesson. every agent. every team." in Inter Tight.
Restrained, confident, editorial. Stripe Press / Linear launch
aesthetic. No glow, no neon, no chrome.

negative prompt: glowing nodes, neon, chrome, cyberpunk, futuristic,
holographic, robot brain, AI cliché, hands on keyboard, terminal
window, gradient mesh, 3d render, cinema 4d, blender default,
overdesigned, cluttered, motion blur on whole image, lens flare,
bokeh balls, dramatic lighting, vignette
```

### B — Bolder (a small surprise)

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

negative prompt: glowing thread, neon, chrome, futuristic, holographic,
3d render, robot, AI cliché, hands, terminal, gradient mesh, cinema
4d, blender default, lens flare, bokeh, vignette, dramatic lighting,
photorealistic skin, cartoon, doodle, whiteboard
```

### C — Weirdest defensible (high upside)

```
A 1:1 editorial still on deep graphite (#0E1116). Centred: a single
oversized perforated receipt — like an old-school dot-matrix
continuous-form receipt with sprocket holes down both edges —
unfurling vertically from the top of the frame. The receipt is
matte off-white paper (#EDEFF4), printed in monospaced type
(JetBrains Mono). At the top of the visible portion, the title
"retry with jitter" is set larger in Inter Tight. Below it, the
receipt segments into four panels separated by perforated tear lines.
Each panel carries the SAME pattern body but a different agent
header: panel 1 — "Claude / agent-07 · payments-prg · 14:02 · ×1.0",
panel 2 — "GPT / agent-11 · billing-brno · +1d · ×1.3", panel 3 —
"Gemini / agent-03 · payments-prg · +2d · ×1.5", panel 4 — "Llama /
agent-22 · checkout-ba · +3d · ×1.95 · CANON". The "CANON" stamp on
panel 4 is cyan-mint (#00E5C7), rubber-stamped over the type. A
single hot magenta (#FF4D8F) tear-line at the third perforation — as
if someone tore off the validated portion. The receipt is the work.
The product is the artefact. Bottom-edge type: "memee — one lesson.
every agent. every team." Aesthetic: Stripe annual report meets a
1970s lab notebook. Archival, mechanical, honest.

negative prompt: glowing receipt, neon, chrome, futuristic, hologram,
3d render, robot, hands, terminal screen, gradient mesh, dramatic
lighting, cinema 4d, blender default, AI cliché, photorealistic skin,
cartoon, doodle, paint splatter, grunge, distressed texture
```

**Picking order if all three render acceptably:** B > A > C.

- B is the highest-craft surprise — the *thread* binding the cards is
  the visual idea. It earns a second look without trying.
- A is the safe shipping default — passes a Linear-tier bar on its own.
- C is the high-variance pick. If the receipt geometry comes out clean,
  it's the most original launch image in the category for years. If
  it comes out muddy, it reads as ambitious-but-confused. Don't ship
  C unless one of the renders is unambiguously beautiful.

---

## 4. If video/animated: 6-frame storyboard (held in reserve)

Loop length: **2.0 s** (X mutes; we optimise for visual storytelling).

| Frame | Time | What's on screen | Change vs prior frame |
|---|---|---|---|
| 1 | 0.00 s | Empty graphite field. A faint guide grid fades in 8 % opacity. | — |
| 2 | 0.30 s | Front card lands at centre with a 6 px settle (no bounce). Type appears already-set; no typewriter effect. Cyan-mint `CANON` stamp lands 80 ms after the card with a single soft thump. | Card arrives. |
| 3 | 0.80 s | First rear card fades up at 70 % opacity, behind-and-to-the-right. Magenta hairline arrow draws from front → first rear in 140 ms. | First propagation. |
| 4 | 1.10 s | Second rear card fades up at 60 % opacity. Second arrow draws. | Second propagation. |
| 5 | 1.40 s | Third rear card fades up at 50 % opacity. Third arrow draws. | Third propagation. |
| 6 | 1.55 s → 2.00 s | All five elements hold. Bottom-edge wordmark `memee — one lesson. every agent. every team.` is already on screen from frame 1; no late reveal. Hard cut to frame 1. | Hold + loop. |

Music cue: **none.** X auto-plays muted; sound is parasitic. If the
post gets unmuted on a desktop with a hover-to-play, a single 2-second
ambient tone (sub-50 Hz, sub-audible felt-not-heard) is acceptable.
Don't ship music — it forces a trailer aesthetic we're rejecting.

Motion principle: cards arrive *settled*, not animated-in. No easing
curves that say "look at me". The arrows are the only line motion.
Linear's launch animations are the reference — motion is punctuation,
not narrative.

---

## 5. One-sentence asset caption (alt text + body line)

```
A single CANON-stamped index card centred on graphite, with three
softer copies behind it — one lesson, four agents, four readers.
```

(118 chars. Works for screen readers; works as the post's text body
sub-line. Can stand on its own.)

---

## 6. The "what NOT to make" list

Five specific anti-patterns. Each is a thing every dev-tool launch
already does and we will not do.

1. **Don't render a Mac Terminal screenshot of `pip install memee`
   followed by a green "✓ success" line.** Every CLI tool launch since
   2018. Reads as derivative. The install command lives in the post
   body, not the image.
2. **Don't make a node-and-edge knowledge graph with glowing cyan
   connections.** "Memory" + "AI" pattern-matches every designer to
   neural-network art. The hero diagram on the site is already the
   network view; doubling it kills the second-look. Receipts beat
   neurons.
3. **Don't put a 3D-rendered glassmorphic database icon next to a
   chat bubble.** This is the literal default ChatGPT-prompt aesthetic.
   We are not that.
4. **Don't show four model logos (Claude, GPT, Gemini, Llama) as a
   horizontal row of brand marks with arrows pointing at a central
   "Memee" wordmark.** It looks like a integrations page on a SaaS
   landing site. We allude to the four families through *card
   metadata*, not through brand-asset logos. (Plus: trademark hassle.)
5. **Don't put a chart of "tokens saved" with a downward green line
   on the launch image.** Numbers belong in the post body and the
   token-math section of the site. The launch image is for the
   *concept*, not the *proof*. Charts on launch posts read as
   investor-deck cosplay.

Bonus rejects: no whiteboard doodle, no isometric "process flow", no
glowing brain, no astronaut metaphor, no rocket emoji ascii, no
"before / after" split image, no hand-on-keyboard, no developer-with-
headphones, no infinite-loop ouroboros for "memory persistence".

---

## 7. The fallback

If the generated cards are mid (typography mush, weird perspective,
the rear cards read as garbage), the safe fallback is a **typeset
terminal artefact, not a screenshot**. Set it as a vector — pixel-
perfect — not captured from iTerm.

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│   $ memee brief --task "retry an idempotent http call"           │
│                                                                  │
│   ── Memee briefing · 38 tokens ─────────────────────────────    │
│                                                                  │
│   PATTERN  retry with jitter                          ×1.95 ✓    │
│   exp backoff, cap 30 s, idempotent verbs only.                  │
│   first seen: Claude / payments-prg · validated 4× / 3 models    │
│                                                                  │
│   ── canon ───────────────────────────────────────────────────   │
│                                                                  │
│                                                                  │
│   memee — one lesson. every agent. every team.                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

Single command. Single result. The result is *small* — 38 tokens,
visible — that's the punchline against a 2,160-token `CLAUDE.md`.
Set in JetBrains Mono on graphite, cyan-mint for the `×1.95 ✓`,
hot magenta for the divider rules, off-white for the body. No
window chrome, no traffic lights. The terminal is implied; the
artefact is the focus.

This fallback is worse than the cards because it reads as "another
CLI tool" instead of "another *kind* of tool". But it's robust: it
will not fail the typography test, and it tells the truth (Memee
is a CLI artefact-producer). If we use it, we lean on the post
body for the conceptual lift.

---

## The one creative concept I'd ship if I had to lock it in 1 hour

**Prompt B — the bound-receipts still life with the magenta thread.**

It is the only one of the three options that contains a genuinely new
visual idea (the thread as the propagation mechanism — physical, not
neural), executes inside the brand language without resorting to the
brand's existing diagram, and survives the 0.5-s thumbnail test as
both a square in the feed and a 200 × 200 quote-tweet preview. A is
safer but indistinguishable from any Vercel launch hero of the last
two years; C is more original but its geometry is fragile under
generation. B is the median of taste and risk, and the thread carries
the entire product thesis — *one lesson, bound to many readers* —
without a single word of explanation.
