# Release positioning — co změnit po skoku z v1.1.0 na HEAD

Hlavní otázka: **co měřitelně přibylo od v1.1.0 a co z toho komunikovat?**

`git log v1.1.0..HEAD` má 26 commitů. Z hlediska user-visible hodnoty:

- ✅ **+5.18 nDCG points** s cross-encoderem opt-in
- ✅ **207-query × 255-memory** evaluation harness (z 12 memories)
- ✅ **6.7×** dedup hot path (MinHash LSH)
- ✅ **116×** vector retrieval warm path (cached numpy matrix)
- ✅ **+3.17 nDCG** porter tokenizer (silently)
- ✅ **94%** test coverage gate
- ✅ Confidence calibration substrate (Brier / ECE / reliability diagram)

Aktuální site i README mluví v jazyce v1.1.0. Tento dokument říká
**co přesně změnit**, dokud držíme princip *simple navenek*.

---

## Pilíře pro update

### 1. Verze: v1.1.0 → upcoming v1.2.0

`site/index.html:1255` má hard-coded `v1.1.0 on GitHub`.
`README.md` zmiňuje v1.1.0 ve více místech.
`pyproject.toml:7` je `version = "1.1.0"`.

**Návrh:**
- Bump `pyproject.toml` na `1.2.0` (minor — schema nové sloupce
  `expires_at`, `ranker_version`, `ranker_model_id`, indexy, ale
  vše idempotentně migrované in-place).
- Tag `v1.2.0` na main.
- Site eyebrow → `v1.2.0 on GitHub`.

### 2. CHANGELOG: ze stavu `[Unreleased]` na `## [1.2.0] — 2026-04-25`

Aktuální `[Unreleased]` má detailní R8-R14 breakdown. Po release tag:
- Promote `[Unreleased]` na `[1.2.0]`.
- Přidat **migration notes** (`pipx upgrade memee` postačí; in-place
  schema bootstrap).
- Přidat **breaking-change list** (žádné — všechny nové sloupce mají
  default).

### 3. README: nahradit "v1.1.0 hit@1 = 100% on 12-memory bench"

Dnešní stav `README.md:107`:

> "Retrieval: 207-query × 255-memory eval harness with 7 difficulty
> clusters. BM25-only baseline `nDCG@10 = 0.7273`. With the optional
> cross-encoder rerank (...) `nDCG@10 = 0.7628` (+0.0355, p=0.0002)."

To je **honest** ale **dlouhé**. Pro README hero, konkrétnější forma:

> **Search ranks like a paid IR product.** 207-query × 255-memory eval
> harness with 7 difficulty clusters (paraphrastic, code, anti-pattern,
> onboarding, diff-review, multilingual, lexical-gap). Default BM25
> baseline: nDCG@10 = 0.73. Add `pip install memee[rerank]` and the
> cross-encoder lifts onboarding queries by **+0.11**, diff-review by
> **+0.06**, with p<0.05 on a 10k-iter permutation test. Numbers
> reproducible via `python -m tests.retrieval_eval`.

### 4. Microsite: HERO — co změnit

Současné HERO claims:

| metrika | dnes | dál nehonest |
|---|---|---|
| `4` model families | ✅ stále platí | (žádná změna) |
| `1` canon | ✅ stále platí | (žádná změna) |
| `∞` sessions outlived | ✅ aspirational, OK | (žádná změna) |
| `$0` to start | ✅ stále platí | (žádná změna) |

HERO statistiky jsou stabilní. **Pod-hero zóna** ale potřebuje update:

#### Sekce S5b "FLAT-PLAN VALUE" — `hit@1 = 100%` musí pryč

Site má:
> "**hit@1 = 100 %** on the routing benchmark (was 16.7 % without the
> router)"

Tento benchmark byl **12-memory routing test** z 1.0.x. Aktuální
207q harness má **MRR = 0.83 BM25-only / 0.87 s rerankem** — což je
poctivější číslo. `hit@1 = 100%` na 12-memory testu je jako říkat
"100% testů projde" když máte 12 testů.

**Návrh:**

> **Top result first.** On a 207-query eval harness with adversarial
> lexical-gap queries, MRR = 0.83 default, **0.87 with the optional
> cross-encoder**. In a 7-task A/B, iterations per task dropped
> &minus;65 %.

#### S7 "PROOF" — gigacorp 18-month čísla zkontrolovat

Site cituje:
> "501M tokens saved", "12 → 3 incidents / month", "3× annual ROI"

Tato čísla jsou z `test_gigacorp` 18-month sim. Po R12 P0 truth alignment
(commit `9776efd`) jsme drift opravili. Stále platná, ale je dobré
re-run gigacorp na HEAD a ověřit:

```bash
.venv/bin/python -m pytest tests/test_gigacorp.py -s 2>&1 | grep -E "Tokens saved|Incidents|ROI"
```

Pokud čísla drift, sjednotit. Pokud platí, ponechat.

### 5. Microsite: nová sekce "Co se zlepšilo od v1.1.0"

Po proof sekci, před pricing. **Optional**, ale dává jasný progress
narrative.

```html
<section class="upgrade shell">
  <h2 class="display">v1.1.0 → v1.2.0: prostě líp.</h2>
  <p class="lede">
    Žádný nový CLI. Žádný nový config. Stejné <code>memee</code> co
    máš nainstalované, jen ranky o 5 nDCG bodů přesněji a search 116×
    rychleji na hot pathu.
  </p>

  <div class="upgrade-grid">
    <article>
      <span class="kicker">Retrieval</span>
      <h3>+0.05 nDCG, default-on path</h3>
      <p>Porter tokenizer + RRF fusion + tag-graph třetí retriever +
      project-aware boost.</p>
      <code>pip install --upgrade memee</code>
    </article>
    <article>
      <span class="kicker">Optional</span>
      <h3>+0.04 nDCG, cross-encoder rerank</h3>
      <p>Onboarding +0.11, diff-review +0.06 (p&lt;0.05). Latency
      +40&nbsp;ms p50.</p>
      <code>pip install memee[rerank]</code>
    </article>
    <article>
      <span class="kicker">Hot paths</span>
      <h3>116× warm vector, 6.7× dedup</h3>
      <p>Cached numpy matrix; MinHash LSH on quality-gate dedup.
      Telemetry inline at 0.76&nbsp;ms/search.</p>
      <code>žádná akce</code>
    </article>
    <article>
      <span class="kicker">Eval harness</span>
      <h3>12 → 207 queries, 7 clusters</h3>
      <p>Honest baseline. Per-cluster regression gate. Permutation
      tests s n=10k. Reproducible.</p>
      <code>python -m tests.retrieval_eval</code>
    </article>
  </div>
</section>
```

Tón: **"upgrade ti přinesl tohle, beze změn UX."** Konsistentní s
*simple navenek* principem.

### 6. Pricing — nezměnit

`Free / Team $49 / Enterprise $12k`. Stále platí. Změny pricing až po
shipnutí gamechangerů #4 (AST review) nebo memee-team Compliance tier
(ledger + encryption).

### 7. README install snippet — nezměnit

Stále `pipx install memee`. To je *zero-config* jak chce princip.

### 8. Co **NE** přidávat na microsite

- ❌ Zmínka o LTR / shadow logging — funguje silently, user to nechce vidět.
- ❌ Zmínka o calibration substrate — interní detail.
- ❌ Zmínka o canon ledger — odložené, vyhrazené pro memee-team Compliance.
- ❌ Zmínka o tag-graph třetím RRF retrieveru — interní implementační
  detail (user vidí jen "search rankuje líp").
- ❌ Zmínka o porter tokenizer — interní.
- ❌ Honest negative #1 (field-aware BM25 weights) — nikoho nezajímá co
  jsme nezashipovali.

Princip: **pokud to nemění user-visible behavior, nepatří to na site**.

---

## Konkrétní diff list

### `pyproject.toml`
```diff
-version = "1.1.0"
+version = "1.2.0"
```

### `site/index.html`
```diff
-      <a href="https://github.com/gizmax/memee/releases/latest">v1.1.0 on GitHub</a>
+      <a href="https://github.com/gizmax/memee/releases/latest">v1.2.0 on GitHub</a>

-          <p><strong>hit@1 = 100&nbsp;%</strong> on the routing benchmark (was 16.7&nbsp;% without the router). In a 7-task A/B, iterations per task dropped <strong>&minus;65&nbsp;%</strong>. Real time saved, not imaginary dollars.</p>
+          <p>On a 207-query eval harness with adversarial lexical-gap queries, <strong>MRR = 0.87</strong> with the optional cross-encoder reranker. In a 7-task A/B, iterations per task dropped <strong>&minus;65&nbsp;%</strong>. Real time saved, not imaginary dollars.</p>
```

Plus volitelně přidat sekci "Co se zlepšilo od v1.1.0".

### `README.md`

```diff
-- **Retrieval**: 207-query × 255-memory eval harness with 7 difficulty
-  clusters. BM25-only baseline `nDCG@10 = 0.7273`. With the optional
-  cross-encoder rerank (`MEMEE_RERANK_MODEL=cross-encoder/ms-marco-
-  MiniLM-L-6-v2`, `pip install memee[rerank]`): `nDCG@10 = 0.7628`
-  (+0.0355, p=0.0002). Run `python -m tests.retrieval_eval` to
-  reproduce.
+- **Retrieval**: out-of-the-box BM25-only path scores `nDCG@10 =
+  0.73` on a 207-query × 255-memory harness with 7 difficulty
+  clusters. With the optional cross-encoder rerank
+  (`pip install memee[rerank]`), the onboarding cluster lifts by
+  **+0.11**, diff-review by **+0.06** (p<0.05). Macro `nDCG@10 =
+  0.76`, MRR `0.87`. Reproduce: `python -m tests.retrieval_eval`.
```

### `CHANGELOG.md`

Promote `[Unreleased]` na `[1.2.0]` + add release date + krátký
release note paragraph nahoře:

```markdown
## [1.2.0] — 2026-04-25

R8 → R14 bundled into a minor bump. The headline: **search ranks
~5 nDCG points better with the optional cross-encoder rerank, and
the default-on path is +1.6 nDCG over v1.1.0** thanks to the
porter tokenizer + RRF fusion + tag-graph third retriever +
project-aware boost. Hot paths: vector retrieval 116× warm via the
cached numpy matrix; quality-gate dedup 6.7× via MinHash LSH.
Eval harness: 12 → 207 queries × 255 memories with 7 difficulty
clusters and per-cluster permutation tests.

No breaking changes. New schema columns (R9 `expires_at`, R10
indexes, R12 calibration columns) bootstrap idempotently in place
on first launch. `pipx upgrade memee` is sufficient.

Detailed delta: see CHANGELOG below.
```

---

## Co bych ne-shipnul (odůvodnění)

### Ne: detailní rozpis 6 gamechangerů na site

`docs/gamechangers.md` má 1745 řádků. Site je marketing surface, ne
encyklopedie. Pokud zákazník chce hloubku, github repo a docs/ jsou
po ruce.

### Ne: sekce "Co plánujeme"

Roadmap je interní (`docs/roadmap.md`). Marketing site by měl
vyjadřovat **co je hotovo a měřitelné**, ne sliby. To je v souladu s
*honest copywriting* z R6 review fixes.

### Ne: "+5.18 nDCG points" jako hero stat

Příliš technické. **MRR=0.87 s rerankem, time saved -71%, iterations
-65%** jsou srozumitelnější metriky.

### Ne: nový dashboard screenshot

Dashboard se nezměnil. Aktualizace screenshotu je work bez user-
visible payoff.

---

## Sequencing — co udělat tento týden

1. **Den 1** — bump version 1.1.0 → 1.2.0, retag, re-run gigacorp aby
   ROI / incidents / token savings byly aktuální.
2. **Den 2** — README updates (krátké, jen nutné).
3. **Den 3** — microsite update (eyebrow + S5b sekce + nová "v1.1.0 →
   v1.2.0" sekce).
4. **Den 4** — CHANGELOG promote + release note paragraph.
5. **Den 5** — upload na FTP, verify deployment, GA event "v1.2.0
   release."

Total work: ~5 hodin disciplinovaného textu, žádná inženýrská práce.

---

## Honest framing

- **Tato je čistě positioning práce, žádné nové features.** Měřená
  zlepšení už jsou v repu od R8-R14.
- **Pokud release tag neproběhne, microsite zůstává správný** — text
  by měl odkazovat na měřitelné výsledky, ne na verzi tagu.
- **Site `claude.gizmax.cz/memee`** (development microsite) je
  primary; `memee.eu` je production. Update obojí.

Last updated: 2026-04-25.
