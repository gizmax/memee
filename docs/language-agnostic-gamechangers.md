# Jazykově agnostické gamechangery — co funguje napříč jazyky

Filter přes 6 gamechangerů z [`docs/gamechangers.md`](gamechangers.md):
**které z nich fungují stejně dobře v češtině, němčině, francouzštině,
japonštině jako v angličtině?** Tabulka přínosů, odhadů, dopadu na
produkt a doporučení.

Konkrétní rozhodovací pravidlo: položka je *plně jazykově agnostická*,
když její mechanismus operuje na **strukturálních datech** (IDs,
timestamps, číselné features, AST uzly, byte-level enkrypce), ne na
přirozeně-jazykovém textu. Položka je *strukturálně agnostická, ale
quality-bounded retriever-em*, když pracuje strukturálně, ale úspěch
závisí na kvalitě upstream retrieveru — který může být jazykově vázán.

## Headline tabulka

| # | Gamechanger | Jazykově agnostický? | Přínos | Odhad | Jak změní Memee | Chceme? | V čem pomůže |
|---|---|---|---|---|---|---|---|
| **#2** | Evidence graph as canon ledger | ✅ **ANO** (struktura, ne text) | Provenance trail, contradiction guard, audit export | +0.01-0.02 nDCG (sekundární); enterprise enabler (primární) | Search engine → **claim ledger** | **ANO** | Compliance, audit, GDPR/SOX/HIPAA — buyer category, kterou dnes nemůžeme obsluhovat |
| **#4** | Neuro-symbolic review (tree-sitter AST) | ✅ **ANO** (AST level + 50+ programming langs) | Precise diff review s evidence chain | +25-30 nDCG na diff_review cluster (estimate, EN-měřený baseline) | review.py = **primary code analysis surface** vedle ruff/mypy/sonar | **ANO** | Polyglot enterprise, agent code review, security gate, deepest moat vs konkurence |
| **#6 (encryption)** | Privacy encryption + audit + retention | ✅ **ANO** (byte-level enkrypce; audit struktura) | Enterprise security posture; SOC2/HIPAA/SOX-eligible | enabler (žádný nDCG) | Open-source tool → **enterprise-ready** | **ANO** | Healthcare, finance, government — cele nové trhy |
| **#5** | Expected-value router | ⚠️ **MOSTLY** (math agnostic; P(rel) bounded retriever-em) | EV-budget routing, token cost shift | indirect (přes calibration) | "saves tokens" → "**maximizes EV under your budget**" | **ANO** ale gated na telemetry | Pricing narrative, budget-conscious customers |
| **#3** | LTR + counterfactual logging | ⚠️ **STRUKTURÁLNĚ** (features agnostic; vector_rank bound by retriever) | Per-team learned ranker, drifts toward team behaviour | +5-10 % hit@1 at convergence | Static ranker → **per-customer model**, retrains nightly | **ANO** ale gated na 500+ events | Operational moat vs Mem0/Zep/Letta |
| #1 | Cross-encoder rerank | ❌ **NE** dnes (EN-only ms-marco model) | +0.0355 macro nDCG měřeno na EN | EN-only out-of-box | Out-of-the-box ranking quality | **ANO** ale s **model swap** na multilingual variant | EN trh ihned; EU/CJK/PL až po swap |

---

## Detailní popis každé položky (jazykově agnostické subset)

### ✅ #2 Evidence graph as canon ledger — plně agnostické

**Proč funguje napříč jazyky.** Mechanismus operuje na
`MemoryConnection(source_id, target_id, relationship_type, expires_at)`
a `evidence_chain` JSON listu. Žádný z těchto datových bodů není
přirozeně-jazykový. `memee why <id>` walks graph edges po
relationship_type a vrací timestamped záznamy z evidence chain —
to fungue identicky pro CS, DE, JA memory.

**Single language-bound subkomponenta:** dream cycle inference
rules (`_infer_dependencies`, `_infer_supersessions`) používají
textual cues v EN (`"requires"`, `"prerequisite"`, `"deprecated in
favor of"`). To je izolované — dá se rozšířit per-language regex
sadou bez dotyku zbytku ledger surface.

**Co to přinese.** Compliance/audit buyer dostává out-of-box pro
provenance trail v jakémkoliv jazyce zákazníka. Konkrétně: německý
healthcare zákazník zadává `memee why pat-dsgvo-art32` (DSGVO = GDPR
v němčině) — výstup je ledger struktura plus textový obsah memory
v jeho jazyce.

**Odhad.** +0.01-0.02 nDCG na canon-heavy queries (sekundární win;
primární je enabler kategorie zákazníků). Žádné měření zatím není —
gated na 2 cykly dream output dat.

**Jak změní Memee.** Z "search engine pro institutional memory"
se stává **claim ledger s search engine na vrchu**. Different
narrative, different buyer (legal/compliance/audit místo dev
manager), different price tier (~$X00/mo Compliance tier).

**Chceme to? ANO.** Hlavní gamechanger pro vstup do regulovaných
odvětví. Plumbing už shipnut v R9 (40 % práce hotovo); chybí read
surfaces (CLI + dashboard graph view + audit export).

---

### ✅ #4 Neuro-symbolic review — plně agnostické

**Proč funguje napříč jazyky.** Tree-sitter má jazykové packy pro
50+ **programming languages** (Python, JS, TS, Go, Rust, Swift,
Kotlin, Java, C/C++, Ruby, PHP, …). Každý pack je gramatika +
parser. AST matching operuje na strukturálních uzlech
(`call.function = "requests.get"`, `call.kwargs missing "timeout"`)
— **přirozený jazyk komentářů ani jazyka uživatele není ve hře**.

Memory který se matchne (anti-pattern `ap-http-no-timeout`) má svůj
`AntiPattern.detection` pattern uložený jako strukturální:
```json
{"call": "requests.get", "missing_kwarg": "timeout"}
```
Ne jako EN regex. Jeho `title` a `consequence` jsou v jazyce, ve
kterém ho zaznamenal autor (CS uživatel zaznamená anti-pattern v CS,
match je strukturální, warning text je v CS).

**Odhad.** +25-30 nDCG na `diff_review` cluster (n=30, BM25-only
baseline 0.5557, cross-encoder 0.6192, AST review estimate 0.85+).
**Největší měřený headroom** přes všechny gamechangery. Tento
estimate platí napříč programming languages — jakmile language pack
existuje, AST precision je consistent.

**Jak změní Memee.** review.py z "nice to have keyword scanner"
na **primary code analysis surface** vedle ruff/mypy/bandit/sonar.
Unique competitive moat: žádný konkurent (CodeRabbit, Greptile,
Codeium) nemá současně AST + organizational memory + agent feedback
loop. AST je generic; team-specific memory je proprietary substrate.

**Chceme to? ANO — největší ROI z měření.** Tree-sitter je known
quantity (stable since 2018, používá ho Zed/GitHub/Neovim). 2-3
týdny pro Python+JS+TS v1; 3-4 dny per další jazyk.

**Per-language coverage roadmap:**

| Phase | Languages | Wheel size | Cumulative |
|---|---|---|---|
| v1 | Python | +5 MB | 5 MB |
| v2 | + JS, TS | +12 MB | 17 MB |
| v3 | + Go, Rust | +15 MB | 32 MB |
| v4 | + Swift, Kotlin | +12 MB | 44 MB |

OSS user dostane `[review-python]` light extras; `[review-all]`
pro polyglot enterprise.

---

### ✅ #6 Privacy encryption + audit + retention — agnostické (encryption část)

**Proč funguje napříč jazyky.** SQLCipher (encryption-at-rest)
operuje na byte úrovni — AES-256-CBC s HMAC-SHA512. Žádná
jazyková rovina. Audit log (`MemoryAccessLog`) je strukturální:
`(user_id, memory_id, action, timestamp, ip)`. Retention policy
je time-based: `retention_until < utcnow() → redact`.

**Single language-bound subkomponenta:** PII redakce regex pravidel
(US SSN ≠ EU IBAN ≠ JP My Number). Tato část je v
`docs/multilang-team.md` zpracovaná samostatně jako item #5
(locale-aware PII redaction).

Encryption + audit + retention jsou **plně agnostické**; PII
redakce je locale-bound. Tu část lze plánovat odděleně bez
blokování encryption layeru.

**Odhad.** Žádný nDCG win (není to ranker upgrade). Ale:
- Storage overhead: ~5-10 % search latency (přijatelné).
- Wheel size: +30 MB (SQLCipher).
- Customer category enabler: healthcare, finance, government, EU
  regulated.

**Jak změní Memee.** Open-source tool → **enterprise-ready substrate**.
SOC2 Type II audit becomes possible. HIPAA covered entity
eligibility. SOX control evidence (kombinace s #2 canon ledger).
GDPR Article 17 right-to-erasure: `memee privacy erase --user
alice@example.com`.

**Chceme to? ANO — gated na first regulated prospect.** 2-3 týdny
implementace. Inverted build/ask: kdo postaví první, vyhrává deal.
Ale spekulativně? To je product decision — pokud máme pipeline,
build it.

---

### ⚠️ #5 Expected-value router — mostly agnostické

**Proč mostly:** matematika `EV = P(rel) × impact − tokens ×
token_price` je čistě numerická. impact (application_count,
mistakes_avoided, validation_count, project_count) jsou čísla.
token_price je dollarová částka. tokens je char_count/4 — funguje
pro Latin, Cyrillic, CJK. **Math je language-agnostic.**

**Quality-bound retrieverem.** P(rel) přichází z calibrated
cross-encoder skóre. Cross-encoder dnes je EN-only (#1). Pro CS/DE/JA
queries je P(rel) shifted/biased; EV math běží, ale na špatných
pravděpodobnostech. **Po multilingual cross-encoder swap** (item #1
v `docs/multilang-team.md`) se P(rel) stane jazykově agnostickou →
cele řešení se stane plně agnostickým.

**Odhad.** Indirect přes calibration. Po multilingual swap a
calibration data fill: token cost reduction 30-60 % na typický
briefing.

**Jak změní Memee.** "saves tokens by routing" → **"maximizes
expected value under your budget"**. Different sales conversation:
zákazník konfiguruje `token_price` per model, vidí math per
briefing v dashboardu, věří číslům.

**Chceme to? ANO — gated na telemetry volume + multilingual cross-
encoder swap.** Stack dependency: nejdřív #1 multilingual model
swap, pak calibration data fill (R12 P1 substrate), pak EV router
top.

---

### ⚠️ #3 LTR + counterfactual logging — strukturálně agnostické

**Proč strukturálně:** features jsou numerické / kategorické:
- `bm25_rank` (int) — language-agnostic
- `vector_rank` (int) — bound by embedding model language coverage
- `rrf_score` (float) — agnostic
- `confidence_score`, `validation_count` — numeric
- `maturity`, `type` — kategorické (4-5 hodnot)
- `query_length_chars` — works for any script
- `has_question_mark` — works for any script (ASCII `?`)

**Quality-bound retrieverem.** Pokud underlying bi-encoder a
cross-encoder jsou EN-trained, vector_rank pro CS query je noisy
→ LTR se naučí váhy které kompenzují tu noise, ale **strop je
horší** než kdyby retriever byl multilingual.

**Co dělá multilingually.** LTR sám se naučí per-team preference.
CS tým akceptuje memories tagged `bezpečnost` častěji než
`security` — LTR to zachytí pres tag overlap features. Per-tenant
model je per-tenant ranker, nezávisle na jazyce queries.

**Odhad.** +5-10 % hit@1 at convergence (literature standard pro
LambdaMART na pairwise data). Convergence vyžaduje 500+ accepted
SearchEvents per tenant.

**Jak změní Memee.** Static ranker constants tuned by Memee
maintainers → **per-customer model retrained nightly**. Loop
competitors don't have. CS i DE i EN tenant dostane svůj LTR
model; jeho efektivita je horší v non-EN dokud retrievery nejsou
multilingual.

**Chceme to? ANO — gated na volume.** Plumbing už shipped v R9;
chybí counterfactual logging columns + retrain cron + canary
metrics + operator UX.

---

### ❌ #1 Cross-encoder rerank — language-bound dnes

**Proč ne dnes.** `cross-encoder/ms-marco-MiniLM-L-6-v2` je
trénovaný na MS MARCO — anglickém datasetu. Pro non-EN queries:

- Latin script (CS/DE/FR/ES/PL): **degraded quality** — model
  zpracuje tokeny ale nerozumí semantice. Měřeno na 207q harness:
  `multilingual_lite` cluster (n=20, mix CS+DE) cross-encoder Δ =
  +0.0191 (n.s.).
- Non-Latin (JA/ZH/AR/HE): **fails fast** — tokeny jsou OOV, skóre
  je random.

**Multilingual variant existuje.**
`cross-encoder/ms-marco-multilingual-MiniLM-L12-v2` (50+ jazyků,
+200 MB wheel). Swap je 1 řádek v `MEMEE_RERANK_MODEL` env varu.

**Co to znamená.** Tato položka je v naší top-3 gamechangerů (R14
#2 už shipnut s p=0.0002 macro), ale **default-on tah, který
otevírá EU enterprise**, vyžaduje multilingual model. Trade-off:

| variant | wheel size | EN nDCG | non-EN nDCG | EU enterprise demo? |
|---|---|---|---|---|
| ms-marco-MiniLM-L-6-v2 (dnes) | 80 MB | 0.7628 | ~0.7273 (žádný lift) | ❌ failed demo |
| ms-marco-multilingual-MiniLM-L12-v2 | 280 MB | ~0.755 (-0.008 EN) | +0.05-0.10 estimate | ✅ |

EN trade-off je <1 nDCG point; gain v EU je +5-10 nDCG. Net win
pro multilingual swap.

**Chceme? ANO — multilingual variant default-bundled.** Doporučení
v `docs/multilang-team.md` (item #1) i tady.

---

## Souhrn: které tři chceme PRVNÍ

Stack-rank z perspektivy *language-agnostic + impact + ready-to-ship*:

### 1. ⭐ #4 Neuro-symbolic review (tree-sitter AST)
- **Plně agnostické** napříč programming i natural languages
- **Největší measured headroom** (+25-30 nDCG na diff_review)
- **Ready to build** — tree-sitter stable, plánováno v gamechangers.md
- **Effort: 2-3 týdny pro v1 (Python + JS/TS)**
- **Gate: žádný** — můžeme začít hned

### 2. ⭐ #2 Evidence graph as canon ledger
- **Plně agnostické** (struktura, ne text)
- **Deepest narrative shift** — nový buyer (compliance)
- **40 % plumbing už hotovo v R9**
- **Effort: 3-4 týdny pro CLI + dashboard + audit export**
- **Gate: 2 cykly dream output dat** (4-6 týdnů organic data)

### 3. ⭐ #6 Privacy encryption layer (encryption část)
- **Plně agnostické** (byte-level)
- **Enterprise enabler** pro regulated industries
- **Žádný measured nDCG win**, ale category opener
- **Effort: 2-3 týdny pro SQLCipher + audit log + retention**
- **Gate: first regulated prospect** OR strategic decision to build speculatively

---

## Co tato analýza neříká

- **#5 EV router a #3 LTR jsou skvělé**, ale jejich kvalita v non-EN
  je **shora ohraničená** kvalitou underlying retrieveru. Bez
  multilingual cross-encoder swap (#1) jsou výrazně slabší v non-EN
  scenáriích. Sequencing: nejdřív #1 multilingual swap, pak #5/#3.
- **#1 cross-encoder default-on s English-only modelem je
  promarněná příležitost pro EU.** Pokud spustíme default-on s EN
  modelem, postavíme na špatný precedent. Doporučení: **vůbec
  neflipovat default-on dokud není multilingual variant
  default-bundled**.
- **PII redakce z #6 je locale-bound** a je separátně zpracovaná v
  `docs/multilang-team.md`. Encryption layer (SQLCipher + audit +
  retention) je plně agnostický a může jít první.

---

Last updated: 2026-04-25.
