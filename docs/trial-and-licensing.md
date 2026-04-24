# Team trial + licensing — three delivery paths

**Status:** plan. To be implemented in Phase 2, after the public OSS launch.

The `memee-team` package is proprietary and licence-gated. The OSS `memee` is
MIT and needs no key. This document is about how the *paid team tier* is
delivered — the mechanics of a 14-day trial, the licence file, and the billing
loop. Three options, pragmatic order, with recommendations.

---

## Path A — manual JWT trial (ship first, keeps work close to first customers)

**Flow**
1. Form submission on memee.eu lands in `info@memee.eu` (FormSubmit — already
   live).
2. Within 24 h, reply by hand. The reply contains:
   - A 14-day JWT licence key
   - `pip install` instruction with the private-index URL + key
   - A short cold-open question: *"Two sentences — what's the team and what
     problem are you hoping Memee solves?"*
3. Customer sets `MEMEE_LICENSE_KEY=<jwt>` and runs. Team scope unlocks.
4. Day 12: personal follow-up email. *"You've been running Memee Team for
   12 days — any friction? want to continue?"*
5. If yes → invoice, accept payment (Stripe Payment Link, wire, whatever),
   issue a 12-month JWT. If no → JWT expires silently on day 14.

**What we build (half a day)**

- `scripts/issue_license.py` — CLI that signs a JWT with our ed25519 private
  key:

  ```bash
  ./scripts/issue_license.py \
    --sub "Acme Corp" \
    --seats 5 \
    --features team_scope,org_scope,sso,audit \
    --days 14 \
    --trial
  # prints the JWT; copy into email
  ```

- `docs/issue-license.md` — internal runbook for the operator (Tom / any
  future teammate): where the key lives, how to rotate, what claims to set
  per tier.
- Email templates (plaintext, copy-paste friendly):
  - `welcome-trial.txt`
  - `day-12-checkin.txt`
  - `trial-expired.txt`
  - `trial-paid.txt`

**Why start here:** zero infrastructure. Every trial is a real conversation
with the first 10–50 paying teams — priceless learning you can't buy later.
When manual ops start to hurt (probably around 20 trials / month), move to
Path B.

**Pros:** ship tomorrow; every customer feels bespoke; no fraud risk.
**Cons:** doesn't scale; operator (you) becomes a pager.

---

## Path B — Stripe self-serve trial (ship when A gets painful)

**Flow**
1. Pricing page `Start 14-day trial` button → Stripe Checkout (subscription
   with `trial_period_days=14`).
2. Customer enters card (not charged for 14 days).
3. Stripe webhook `checkout.session.completed` fires → our endpoint:
   - Calls the same `issue_license.py` internally
   - Emails the JWT via Resend (~$0/month up to 3 k/day)
4. Stripe webhook `invoice.paid` on day 15 → new JWT with 12-month expiry,
   email it as a renewal.
5. Stripe webhook `customer.subscription.deleted` (trial cancel or paid
   cancel) → no renewal. Current JWT still runs until its `exp`.

**What we build (2–3 days)**

- Stripe products + prices (Team: $49/mo flat up to 15 seats; Growth: custom
  between 15 and 100 seats; Enterprise: from $12k/yr, negotiated). Trial
  period configured on the Team subscription (14 days, no card captured
  if we use payment-link style). Growth and Enterprise stay assisted.
- Webhook endpoint. Three options by effort:
  - **Cloudflare Worker** ($0/month): 80 lines of TypeScript calling an
    edge-deployed `issue_license` equivalent (ed25519 sign via WebCrypto).
  - **Vercel Function** (free tier): same thing in Python/Node.
  - **Tiny FastAPI on memee.eu**: if the hosting supports Python CGI.
    Probably not on WebGlobe shared — check first.
- Persistence: SQLite file next to the webhook, mapping
  `stripe_customer_id → {last_jwt, seats, features, expires}`. Needed to
  regenerate on renewal without asking Stripe for historical metadata.
- Email template variants auto-filled from Stripe metadata.

**Pros:** 24 / 7 self-serve; no operator involvement; Stripe handles card
security; customers see a standard trial UX.
**Cons:** infrastructure to maintain; fraud risk (bots using throwaway cards
to farm trials); worse conversion intel (no personal touch).

---

## Path C — support-only open core (keep the code open, sell the service)

**Flow**
1. `memee-team` code is still proprietary but **not licence-gated at runtime**.
   Importing it just works — no JWT check.
2. What paying customers actually buy:
   - Private Slack channel with the maintainers
   - 24 h email SLA
   - Help with migrations / incidents
   - Hosted Postgres if they want (~$25–50 / month of our cost per customer)
   - Invoices, contract, DPA — things procurement actually cares about
3. The code being copyable is not a bug: anyone who wants to self-host
   without paying does so at their own risk; the buyers are buying
   certainty + relationship.

**What we build (variable)**

- Replace the licence-gate in `memee_team/license.py` with a no-op (or
  remove it entirely).
- Set up a shared Slack workspace / Discord.
- Write an SLA page.
- Set up invoicing (Stripe Invoices or plain EU billing via Pohoda etc.).

**Precedent:** Plausible Community Edition, older HashiCorp, Sidekiq Pro's
honor-system licence (before they added enforcement).

**Pros:** zero fraud risk (no gate to break); enterprise-friendly (no "why
can't I read my own software?"); simplest code.
**Cons:** margins are worse (service is labor); you can't lock in customers
who decide to "just run it themselves" — they keep the features forever.

---

## Recommendation

Ship Path A this week, so we can start collecting paying customers the day
after the OSS launch. Migrate to Path B when Path A turns into 3+ hours per
week of operator time. Keep Path C as an offer for enterprise deals that
demand "source-available, honour-system" — it's a lever, not a default.

## Minimal artifacts needed before Phase 2 begins

- [ ] Generate real ed25519 keypair, store private key in 1Password / password
  manager, embed the public key in `memee_team/license.py` (replace the dev
  placeholder).
- [ ] `scripts/issue_license.py` — see Path A.
- [ ] Four email templates in `docs/email-templates/` — see Path A.
- [ ] `docs/operator-runbook.md` — what to do when a trial lands, when it
  expires, when it converts.
- [ ] Private PyPI (or a signed-wheel download URL) serving `memee-team` to
  valid licence keys only. Simplest: a `pypiserver` behind basic auth with
  the JWT as password; or tarball URLs signed by the same ed25519 key.

None of these block the public OSS launch. They gate on the first paying
team calling.
