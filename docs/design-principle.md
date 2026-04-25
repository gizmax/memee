# Design principle — simple navenek, sofistikovaný uvnitř

> "Idealně bych memee držel na první pohled simple, ale sofistikovaný
> uvnitř. Zároveň co nejméně či nula zásahů od uživatelů. Prostě funguje
> a skvěle."

Tento dokument je re-evaluace roadmapu skrz tento princip. Zero-config
+ no surface area changes je fundamentální constraint, který mění
ranking gamechangerů z `docs/gamechangers.md`, `docs/roadmap.md` a
ostatních.

---

## Co tento princip znamená v praxi

**Simple navenek:**
- Žádné nové CLI commands pokud nejsou nezbytné.
- Žádné nové env vars které musí user nastavit pro to aby to fungovalo.
- Žádné nové dependencies které user musí pip-install-ovat.
- Žádné nové config soubory.
- Žádné `--mode foo` flagy.

**Sofistikovaný uvnitř:**
- Inteligentní auto-detekce co user chce.
- Adaptivní chování na základě measurements.
- Self-tuning parametry.
- Učení z telemetrie bez expicitní akce.

**Zero zásahů od uživatele:**
- Defaultně *nejlepší možný stav* z plně instalovaného balíčku.
- Když je optional dep installed, automaticky use.
- Když není installed, transparent fallback.
- Žádné "spustit migrate před prvním searchem."

---

## Re-ranking gamechangerů skrz princip

Z `docs/gamechangers.md` mělo původní pořadí #1, #4, #2, #3, #5, #6.
Filter "simple + zero-config":

| # | gamechanger | proxy "viditelnost" pro usera | proxy "user effort" | fit s principem |
|---|---|---|---|---|
| **#1** Cross-encoder default-on | žádný — ranking quality jen lift | nula (auto-detect [rerank] extra) | ✅✅✅ **perfect fit** |
| **#3** LTR + counterfactual | žádný — ranker se učí silently | nula (canary auto-routing) | ✅✅✅ **perfect fit** |
| **#5** Expected-value router | malý — nový mode-flag (volitelný) | malý (token_price config) | ✅✅ **dobrý fit** s defaulty |
| **#4** Neuro-symbolic review | mírný — review.py výstup je v jiném formátu | nula (auto-detect tree-sitter extra) | ✅✅ **dobrý fit** |
| **#6** Privacy encryption | velký — nové key management | velký (key generation, rotation) | ❌ **mismatch** — vyžaduje admin |
| **#2** Evidence graph as ledger | velký — 5 nových CLI commands + dashboard view | velký (manual review konfliktů) | ❌ **mismatch** — vyžaduje human-in-the-loop |

---

## Detailní hodnocení každého

### ✅✅✅ #1 Cross-encoder default-on — perfect fit

**Proč fit:** user neudělá *nic*. `pip install memee[rerank]` je
volba (a my můžeme bundlovat model do main wheelu); pokud je dep
přítomný, search se zlepší o +0.0355 nDCG@10. Žádný mode flag,
žádný env var, žádná migrace. **Ranking se zlepší tiše**.

**Co user zažije:** najednou hledání vrací relevantnější výsledky.
Nezeptá se proč; prostě to funguje líp.

**Implementace v zero-config stylu:**
1. Detekce: pokud `sentence-transformers` importable AND model file
   existuje v wheel/cache → enable.
2. Default model: `cross-encoder/ms-marco-multilingual-MiniLM-L12-v2`
   (50+ jazyků, zero degradation pro CS/DE/JA users).
3. Latency cost: hidden za ~40ms p50, agent loops to neuvidí.
4. **Žádný env var nutný**. `MEMEE_RERANK_MODEL` zůstává jako
   override pro power users.

**Doporučení**: ⭐ **ship první**. To je ten kanonický příklad simple
navenek + sofistikovaný uvnitř.

---

### ✅✅✅ #3 LTR + counterfactual logging — perfect fit

**Proč fit:** ranker se učí *silently* z user acceptance. User
nikdy nezadá příkaz "trénuj ranker"; nightly cron to dělá za něj.
A/B canary routing je automatický (10% traffic do candidate model);
regression detection je automatic (auto-revert na hit@1 drop > 5%).

**Co user zažije:** po 6 týdnech používání produkt rankuje líp pro
jeho team. Nezadá nic. Po 12 týdnech rankuje ještě líp. Self-improving.

**Implementace v zero-config stylu:**
1. SearchEvent telemetry už je on by default (R9 plumbing).
2. Background daemon thread retrains nightly, žádný cron user-facing.
3. Auto-promotion: candidate → canary (10%) → production gated na
   `nDCG@10 ≥ baseline AND hit@1 ≥ baseline`.
4. Auto-revert pokud production model regresí.
5. **Žádné CLI commands user-facing**. `memee ranker train` zůstává
   internal/admin tool.

**Doporučení**: ⭐⭐ **ship po #1, gated na 500+ events** (přirozená
maturity, ne user-facing constraint).

---

### ✅✅ #5 Expected-value router — dobrý fit s defaulty

**Proč mírný fit:** matematika je sofistikovaná uvnitř, ale výstup
je *jiný* než dnes — některé memories se nezobrazí kvůli EV gate,
což user může pozorovat a být zmaten.

**Co user zažije:** dnes briefing obsahuje 7 memories; po EV routeru
možná 5 (excluded jsou ty s EV/token < threshold). User si může
říct "kde jsou ostatní?"

**Implementace v zero-config stylu:**
1. Default mode: full ranking (jako dnes). EV výpočet běží na pozadí
   ale neexcluuje, jen reorder-uje.
2. Default `token_price` z built-in tabulky (Sonnet/Haiku/GPT-4o
   prices baked in, refresh při release).
3. Default `min_ev_threshold = 0` — nic nezmizí.
4. Power user může přes env var/config aktivovat strict EV gating.

**Co je trade-off:** sofistikované uvnitř (EV math, calibration), ale
**user-visible behavior je v default módu identical s dneškem**.
Power users získají hodnotu, baseline users neutrpí.

**Doporučení**: ⭐ **ship po #3** s konzervativními defaulty. EV math
funguje silently na pozadí; aktivní filtering až později.

---

### ✅✅ #4 Neuro-symbolic review — dobrý fit s auto-detect

**Proč fit:** review.py se zlepší o AST-aware warnings, které jsou
*přesnější* než dnešní regex warnings. User dostane lepší output bez
změny příkazu.

**Co user zažije:** `memee review --diff` vrací stejný JSON shape
jako dnes, ale `match_method: "ast"` field hodnotí 0.99 confidence
místo 0.6. Strukturovaný `match_location: {file, line, kind}` je nový.

**Co je riziko:** pokud user má skripty co parsují review output,
změna struktury může break. Ale review je relativně mladá feature,
málokdo má scripts.

**Implementace v zero-config stylu:**
1. Detekce: pokud `tree-sitter` importable AND language pack pro
   detected file extension → use AST.
2. Pokud nedetekováno → fallback na keyword regex (today's behavior).
3. Per-language wheel bundle: `[review-python]`, `[review-all]`.
4. `[review-python]` může být v default `[all]` extra, takže
   `pip install memee[all]` dá Python AST review out-of-box.
5. **Žádný env var nutný**.

**Doporučení**: ⭐⭐⭐ **ship paralelně s #1** — má největší measured
headroom (+25-30 nDCG na diff_review cluster), zero-config feasible,
backward-compatible output.

---

### ❌ #6 Privacy encryption — mismatch s principem

**Proč mismatch:** encryption-at-rest fundamentálně **vyžaduje key
management**. User musí:
1. Vygenerovat master key.
2. Někde ho bezpečně uložit (HSM, password manager).
3. Při ztrátě klíče přijít o data (nebo používat seed phrase).
4. Při key rotation re-encryptovat DB.

To je *opak* zero-config. Compliance buyer chce audit trail; ten
vyžaduje konfiguraci. Tato funkce má jiný target market (regulated
enterprise) a její UX není určen pro "prostě funguje" princip.

**Co s tím:** **odložit do memee-team only**. memee-team už má
admin UI (organizace, users, scopes). Tam patří encryption +
retention policy + audit log. OSS Memee zůstává simple.

PII redakce je trochu jiná — to lze udělat zero-config přes
heuristics. Můžeme shipnout:
- Auto-detect PII (email, secret keys) before record → silent redact
  s warning v output.
- Default-on, žádný env var.

To je sofistikované uvnitř (regex + context-aware heuristics) ale
simple navenek (user nemusí nic).

**Doporučení**: 🔄 **rozdělit:**
- **PII auto-redakce** — ✅ ship default-on v OSS (zero-config)
- **Encryption + audit + retention** — ⏸ memee-team only (vyžaduje
  admin)

---

### ❌ #2 Evidence graph as canon ledger — mismatch s principem

**Proč mismatch:** ledger surface je celá nová UX vrstva. 5 CLI
commands. Dashboard graph view. Human review pro contradicts.
**Funkčně je to obrovské, UX-wise ale je to nová mental model**
(canon jako graf state, ne flat list).

User dnes řekne `memee search foo` a dostane výsledky. Po ledger
shipu chce vidět provenance, contradikce, timeline → nové paradigm.

**Co simulace ukázala:** ledger detekuje 6 contradikcí v 12-memory
canonu (50% inconsistency). To je **value pro operator**, ne pro
běžného user-a co jen hledá memories.

**Co s tím:** v souladu s tvým principem — **odložit do memee-team
admin UI**. Compliance buyer (regulated enterprise) chce ledger;
běžný OSS user ho nepotřebuje.

OSS Memee může mít minimal pasivní surface:
- `memee canon-state` jen ukáže #canon vs #conflicts (bez detailů).
- Žádný memee why / timeline / provenance v OSS CLI.
- Dashboard graph view je memee-team only.

**Doporučení**: 🔄 **odložit ledger surface, ale ne plumbing**:
- R9 plumbing zůstává on (depends_on, supersedes inference) — to
  zlepšuje ranker silently.
- `engine/canon_ledger.py` modul je hotov, ale exponovat ho ne
  v OSS CLI. Použijeme ho v memee-team admin UI později.
- OSS user neví že ledger existuje. Memory funguje jako dřív, jen
  *uvnitř* je sofistikovaný graf.

---

## Nový ranking — co shipovat v souladu s principem

### Ship priority (re-ranked)

1. ⭐⭐⭐ **#4 Neuro-symbolic review** (auto-detect tree-sitter, lepší
   output, backward-compatible). Largest measured headroom.
2. ⭐⭐⭐ **#1 Cross-encoder default-on** (multilingual variant
   default-bundled, auto-detect). Lowest-risk shipped opt-in already.
3. ⭐⭐ **#3 LTR silent self-improvement** (nightly retrain daemon,
   auto canary, auto revert). Gated na telemetry volume.
4. ⭐ **PII auto-redakce** (z #6, jen redakce část; default-on
   regex heuristics). Zero-config, silent.
5. ⭐ **#5 EV router shadow mode** (default mode = no behavior change;
   matematika běží silently na pozadí, později aktivní filtering).

### Defer priority

- **#2 Evidence graph ledger** — modul hotov, ale **OSS surface odložen**.
  Plumbing běží silently a zlepšuje ranker; CLI/dashboard exposure
  pouze v memee-team admin UI (= compliance tier).
- **#6 Encryption + audit + retention** — odložit do memee-team. OSS
  Memee zůstává plain SQLite (nicméně user může klást na disk encryption
  na úrovni OS).

### Co tahle re-evaluace mění proti `docs/gamechangers.md`

Předchozí ranking dle "biggest narrative shift":
> #1 cross-encoder, #2 evidence graph, #3 LTR, #4 AST review, #5 EV, #6 privacy

Nový ranking dle "simple navenek + zero-config":
> #4 AST review, #1 cross-encoder multilingual, #3 LTR silent, **rest deferred / split**

Hlavní změna: **#2 evidence graph ledger byl ★★★ deepest narrative
shift**, ale UX přidává surface area a vyžaduje human-in-loop pro
contradicts review. To narušuje princip. Ledger zůstává jako interní
sofistikace (silently zlepšuje briefing přes depends_on), ale ne
jako user-facing CLI/dashboard ve OSS.

---

## Co tato strategie přinese

### Ze zákazníkova pohledu

**Před:** "nainstaluju Memee, nakonfiguruju 5 env vars, naučím se 8
nových CLI commands."
**Po:** "pip install memee, použiju, je to jen líp."

Konkrétně user-visible changes:
1. Search rankuje líp (cross-encoder + LTR ticha).
2. Review fires přesnější warnings (AST místo regex).
3. PII se silently redakuje (žádný leak do embedding nebo evidence).
4. Briefing dává relevantnější memory (LTR per-team).

**User akce, které se nezvyšují:** žádná nová CLI command, žádná
nová config option, žádný nový env var co musí být nastaven.

### Z developerova / kontributora pohledu

**Před:** 6 gamechangerů × 5 surfaces (CLI / dashboard / env vars /
config files / API) = 30 surface points to maintain.
**Po:** 4 silent improvements + 2 deferred = 4 surface points to
maintain (jen optional dep auto-detect).

Maintenance load zůstává nízký. Žádná deprecation cycles na CLI
commands kterým neudělíme product priority.

### Z business pohledu

OSS Memee **drží svůj core value prop** ("AI memory co prostě funguje"),
zatímco memee-team **rozšiřuje na compliance/admin tier** s feature
co OSS uživatel nepotřebuje vidět.

To je clean rozdělení:
- OSS = power tool pro AI agents, simple
- memee-team = enterprise admin platform with advanced surfaces
  (ledger, encryption, audit, retention)

Pricing/value coupling:
- OSS: free, simple, sophisticated.
- memee-team: $49/mo Team (multi-user + scoping).
- memee-team Compliance tier: $X00/mo (ledger + audit + retention +
  encryption).

---

## Concrete next steps (3-měsíční plán)

### Týden 1-2: #4 AST review v1 (Python)
- Auto-detect tree-sitter, fall through to regex.
- Bundle Python language pack do `[review]` extra (rename z
  `[rerank]` že to lépe odráží: review = AST review).
- Backward-compatible JSON output, jen `match_method: "ast"` field
  je nový.

### Týden 3: #1 Multilingual cross-encoder default-on
- Swap default model na `cross-encoder/ms-marco-multilingual-MiniLM-L12-v2`.
- Bundle do `[rerank]` extra (250 MB wheel).
- Auto-enable when extra installed.

### Týden 4-6: #4 AST review v2 (JS/TS)
- Add JavaScript + TypeScript language packs.
- Same auto-detect logic.

### Měsíc 2: PII auto-redakce
- Default-on regex heuristics pro email, secrets, AWS keys, JWT.
- Locale-aware: detect EU formats by org locale (silent, no config).
- Silent redact + warning na pozadí.

### Měsíc 3: #3 LTR silent self-improvement
- Background daemon thread retrains nightly.
- Auto canary routing (10% traffic).
- Auto-revert na regression.
- **Zero user-facing CLI commands.**

### Q2+: deferred items
- #5 EV router shadow mode (math běží, defaultně neexcluuje).
- #2 ledger v memee-team admin UI (ne OSS).
- #6 encryption v memee-team (ne OSS).

---

## Honest framing

- **Tato re-evaluace odsouvá #2 (ledger), který byl předtím rated ★★★**.
  Argument: ledger value je pro compliance buyer, ne pro běžného OSS
  uživatele. Compliance tier dostává ledger v memee-team; OSS zůstává
  simple. To je přesněji compatible s principem než předchozí "ship
  ledger surface všem."
- **#1, #3, #4 jsou přirozeně silent** — zero-config je jejich
  default mode. To je důvod proč jsou high-priority.
- **#5 EV router je v middle** — sofistikované, ale "simple navenek"
  vyžaduje konzervativní defaulty. Doporučuju shadow mode jako první
  ship.
- **#6 split** je nutný kompromis. PII redakce je zero-config možná;
  encryption fundamentálně vyžaduje key management.

Last updated: 2026-04-25.
