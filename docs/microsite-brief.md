# Memee Microsite — Creative Brief

**For the designer (Claude or human). Do the brief, don't narrate it.**

---

## 0. The one-line ask

A single-page microsite for **Memee** that sounds like Don Draper pitching it — not like a SaaS landing page written by a model. Bold editorial typography, mid-century confidence, zero AI clichés. The kind of site that makes an engineer show it to their boss.

Target URL: `memee.gizmax.cz` (subdomain pattern per the user's existing stack).

---

## 1. Who Memee is (so the copy has backbone)

- **Memee is institutional memory for AI agent teams.** Multi-model (Claude, GPT, Gemini, Llama). Cross-project. It learns what works, remembers what doesn't, and pushes that knowledge to every agent that needs it.
- **Your agents forget. Memee doesn't.** (Use this as a recurring anchor.)
- **Free OSS core on GitHub.** Team + Org tiers for companies that want scope, SSO, and support.
- **Currently 194 tests passing, OrgMemEval score 93.8/100, 96% token reduction vs dumping context, ROI 7–10×.**

## 2. The Draper voice

Don Draper sells nylons by selling nostalgia. Sell Memee by selling **the feeling of an organization that stops making the same mistake**.

Voice rules:

- Declarative. Never hedge.
- Short sentences. Then one long one for rhythm.
- Start from a human truth (people forget), end with the product (Memee doesn't). Never explain the technology before you've earned the right.
- No emoji. No sparkles. No "unlock your potential." No "supercharge." No "AI-powered."
- British-spelling-safe, American-comma-safe. Write it so it reads aloud.
- Rule of three. Always three. *Proven. Shared. Remembered.*
- One word, alone on a line, is allowed.
- Never use the word "revolutionary." Ever.

Draper lines to steal the *shape* of (don't quote — reshape):

> "It's not a wheel. It's a carousel."
> "The best way to handle the future is to create it."
> "Advertising is based on one thing. Happiness."

## 3. Visual direction — "print ad, not SaaS site"

### Palette (pick one of these two, don't mix)

**Option A — Madison Avenue 1962**
- Ink `#111111`
- Cream paper `#F2ECDE`
- Oxblood accent `#8A2B2B`
- Brass highlight `#B08A3E` (sparingly, for rules and monogram only)

**Option B — Stripe Press / Monocle**
- Off-black `#141414`
- Bone `#EAE6DE`
- Signal orange `#D7542B`
- Slate support `#2A2F38`

Either way: **no gradients**, **no glassmorphism**, **no neon**, **no purple-to-pink**. One accent colour. That is the discipline.

### Type

- Display: **transitional / editorial serif** — GT Sectra, Tiempos Headline, Söhne Breit, or Canela. Massive. Tight tracking on headlines (-2%). Drop caps on the opening paragraph.
- Body: same family's text cut, or a grotesk like Söhne, Inter Display, Neue Haas Grotesk. 18–20px base, 1.55 line-height.
- Mono: JetBrains Mono or Berkeley Mono for code + token numbers.
- Two faces max. Three weights max (regular / medium / bold).

### Composition

- 12-column grid, but **break it deliberately**. Pull-quotes cross columns. Imagery bleeds off one edge.
- Thick hairline rules (1px ink) separating sections — like a magazine masthead.
- Footnotes and sidenotes in the margin, numbered. This is the "not obviously AI" tell.
- Generous whitespace. If a section feels too empty, it is probably correct.
- One big idea per screen. Scroll rewards patience.

### Imagery

- **No stock AI illustrations.** No isometric dashboards. No glowing brain. No robot hand touching a human hand.
- **Yes:** black-and-white editorial photography (a rolodex on a wooden desk, a cork board, carbon-paper memos, a library ladder, a typewriter with a Post-it that says *remember*). Licence real photography or shoot it. If synthetic, style it as Kodachrome 1961, not MidJourney.
- Or: abstract letterpress-style marks — monogram M, file-card grids, index tabs.
- Motion: minimal. A single marquee line of text at the hero. Scroll-triggered reveals, slow, one at a time. No parallax confetti.

### Don't-list (ship this to whoever designs it)

- No chat bubbles, no "Ask our AI" widgets.
- No `✨`, `🚀`, `🔥`, any emoji in headlines.
- No three-card-grid "Features" section titled "Why Memee?" with identical icons.
- No autoplaying video hero.
- No cookie banner theatre — one line, honest.
- No testimonials with fake headshots.

---

## 4. Structure — section-by-section

Nine sections. One page. Footer. Done.

### S1 — Hero (Draper opener)

**Layout:** cream full-bleed, thin top rule with the wordmark `MEMEE` left, nav (`Product · Pricing · GitHub`) right. Below, one gigantic serif headline taking 85% of the viewport width, set in two or three lines. Subhead beneath, max 20 words. One primary CTA (`Install from GitHub`), one ghost CTA (`See pricing`).

**Headline candidates (pick one, don't A/B-test this):**

- *Your agents forget. We remembered, so they don't have to.*
- *An organization that never learns the same lesson twice.*
- *The first memory that works across every model you'll ever hire.*

**Subhead (one of these):**

- *Memee is the institutional memory layer for teams running AI agents. Multi-model. Cross-project. Yours.*
- *We built the thing that remembers — so the next agent shows up already knowing.*

**Hero visual:** a single black-and-white photograph of a wooden file-card drawer, half-open, one tab pulled slightly forward. Caption in mono, small: *Fig. 1 — Knowledge, the way it used to be stored.*

### S2 — The problem (one long paragraph, drop cap)

Don't title it "The Problem." Title it **"Every company is two people. The one who solved it, and the one about to solve it again."** Paragraph is 80–120 words, one drop cap, ending on: *Memee is the memo you wish everybody had read.*

### S3 — What Memee actually does

Three columns, one rule between them. Headings in small caps, body in serif.

1. **Records.** Patterns, anti-patterns, decisions, lessons — written once, scored, dated, signed.
2. **Routes.** The right 500 tokens to the right agent at the right moment. Not the whole library.
3. **Remembers.** Confidence scoring, maturity lifecycle, cross-project propagation. It gets more certain the more it is used.

Footnote the token number: *¹ Average context injection across 200-project simulation. Internal, reproducible, `memee benchmark`.*

### S4 — Multi-model (the USP Anthropic can't ship)

Full-bleed section, oxblood background, cream text. One statement:

> **We don't care what your agents run on.**
> Claude validates a pattern. GPT confirms it. Gemini finds the edge case. Llama ships it to staging. Memee keeps score across all four, because a truth proved by two different model families is a truth, and everything else is an opinion.

Side note in the margin, small mono: *Cross-model bonus ×1.3. Cross-project bonus ×1.5. Combined ×1.95. Nothing promotes to canon on one model's word.*

### S5 — The token math

This is where the site earns trust. Big type, mono for numbers.

| | Without Memee | With Memee |
|---|---:|---:|
| Context per task | 14,550 tokens | 500 tokens |
| Per agent-year | $27,000 | $1,100 |
| Savings | — | **96%** |

Caption: *Published 2026. Your mileage varies. Your savings don't.*

Below the table, one Draper line: *We sell fewer tokens. You ship more software.*

### S6 — The bridge (CMAM, for the Claude crowd)

Short section. Two paragraphs.

> **For teams already on Anthropic's managed environment:** Memee syncs its canon — and only its canon — into your Claude Managed Agents Memory store. Your sessions see validated org knowledge in `/mnt/memory/` from turn one, without an MCP call, without a prompt.
>
> We stay the brain. CMAM stays the mailbox.

### S7 — Proof (social, but honest)

A single block quote, serif, italic. No photo. Attribution in small caps.

> *We ran a hundred agents across two hundred projects for eighteen months. Incidents fell from twelve a month to three. Tokens saved: five hundred and one million. That's the whole pitch.*
>
> — **INTERNAL SIMULATION, `test_gigacorp`**, MARKED AS SUCH.

(Honesty is the voice. We don't fake customer quotes.)

### S8 — Pricing

Three columns. No shadows, one `Recommended` ribbon on Team. Thick horizontal rule above. Title: **"Pick your scope."**

Everything is self-hosted. We charge for multi-user coordination and compliance, never for the AI engine — that is identical in every tier. Flat-per-team pricing (not per-seat), because Memee is shared infrastructure, not a per-developer productivity tool.

| | **Free** | **Team** | **Enterprise** |
|---|---|---|---|
| Price | $0 forever | $49 / month flat · up to 15 seats · annual | from $12,000 / year · unlimited seats |
| Licence | MIT, open source | Commercial, per team | Custom MSA + DPA |
| Memory scope | Personal only | Personal + Team + Org | Everything, with governance |
| AI engine (router, quality gate, dream mode, CMAM sync, all 16 modules) | ✓ | ✓ | ✓ |
| Multi-model (Claude / GPT / Gemini / Llama) | ✓ | ✓ | ✓ |
| Identity | Local | SSO (SAML / OIDC), RBAC | SSO + SCIM + SOC 2 Type II |
| Storage | SQLite, local | Postgres / Turso backend | On-prem, air-gap deployment |
| Audit log export | — | ✓ | ✓, SIEM-ready |
| Cross-project propagation | Personal scope | Team + Org | Org-wide, federated |
| Support | GitHub issues | Email, 24h SLA | Dedicated CSM, 4h SLA, quarterly reviews |
| Seats | 1 | up to 15 | unlimited |

Below the table, one line: *Free has every AI feature. Team adds multi-user scope, SSO, and audit. Enterprise adds SOC 2, on-prem, and SLA. 15–100 seats without SOC 2 — custom Growth plan via email.*

Two CTAs: `Install free from GitHub` (primary) · `Start 14-day trial` (secondary) · `Talk to sales` (text link, no button).

### S9 — How to order (keep it boring, keep it fast)

Three lanes, numbered.

**1. Free.** One command.
```
pip install memee && memee setup
```
Link: **`github.com/<...>/memee`**.

**2. Team / Org.** One form. Four fields: company, seats, contact email, preferred start date. Button: **Start 14-day trial**. Copy below: *No card until day fifteen. Cancel by replying to any email.*

**3. Enterprise.** Calendar link to a 20-minute call. Copy: *If you need SSO, self-hosting, or a DPA, we start here.*

### Footer

- Left: wordmark + one line — *Built by a company that got tired of teaching the same lesson to every new model.*
- Middle: `Product · Pricing · Docs · GitHub · Changelog · /cmam`
- Right: *© 2026 Memee. MIT-licensed core. Made for people who ship.*
- Hairline rule above. That's it. No newsletter signup. No cookie popup theatre beyond the legally-required sentence.

---

## 5. Interaction + motion

- One **marquee** line under the hero, scrolling slowly, containing the actual list of patterns Memee has learned in the last 7 days (pulled from `memee changelog`). This is the single clever thing on the page. It proves the product is alive.
- Section reveals: 300ms fade + 12px rise. No spring bounce.
- Pricing tier hover: underline the whole row in accent colour. Nothing else.
- Primary button: filled ink, cream text, zero radius (or 2px max). Hover: swap to accent. No gradient. No shadow. No lift.

## 6. Performance + technical

- Single static page. No SPA framework unless the marquee needs it (prefer plain HTML + a 2KB JS file).
- Fonts self-hosted, subset to Latin + a single ligature set. Target: first contentful paint < 1.0s on 4G.
- Lighthouse 95+ on all four axes.
- Include Google Analytics `G-ZN1K4G2TCZ` in `<head>` (per the gizmax.cz convention).
- Responsive from 360 → 1920. On mobile the serif headline drops one step and the pricing table becomes a vertical stack — **no horizontal scroll**, ever.
- Accessibility: real semantic HTML, focus rings visible, AA contrast on every accent/background pair, alt text on the one photograph.

## 7. Deliverables the designer should return

1. Figma or `.pen` file, one page, all nine sections, desktop + mobile.
2. A single `index.html` + `style.css` + `main.js` bundle, self-contained, drop-in deployable.
3. A copy document (`copy.md`) with every string on the page, for translation / editing.
4. The hero photograph as a real asset (licensed or shot), not a generator output.
5. One-paragraph rationale: why this voice, why this palette. So we can defend it when a stakeholder says *"can we make the hero a bit more fun."* (The answer is no.)

---

## 8. The north-star test

Before shipping: print the hero at A3, pin it to a wall, stand eight feet back. If it reads as a **1962 magazine ad for a filing cabinet** and not as a **2026 AI startup landing page**, we've done it.

If it reads as the latter, start over. That's the courage the brief is asking for.
