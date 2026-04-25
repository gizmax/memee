# Multijazyčná řešení — pro Memee OSS i memee-team

Sada položek, které **současně** zlepšují open-source Memee pro
neanglické uživatele a otevírají memee-team enterprise prodej do EU
a regulovaných odvětví. Každé řešení má dvojí návratnost: měřitelný
retrieval lift + odemčení trhu, který memee-team dnes nemůže
obsluhovat.

Pro detailní kontext jednotlivých gamechangerů viz
[`docs/gamechangers.md`](gamechangers.md). Tento dokument je
průřezová tabulka přes ně z úhlu *multilingual + memee-team*.

## Headline tabulka

| # | Řešení | Pro Memee (OSS) | Pro memee-team | nDCG / dopad | Effort | Gate |
|---|---|---|---|---|---|---|
| 1 | **Multilingual cross-encoder** swap (ms-marco-multilingual-MiniLM-L12-v2) | CS / DE / FR / ES queries dostávají +5-10 nDCG na multilingual_lite | EU enterprise zákazníci s polyglot agent flotami | +0.05-0.10 nDCG na multilingual cluster (odhad) | 1 týden | žádný |
| 2 | **Multilingual embedding backbone** (paraphrase-multilingual-MiniLM-L12-v2) — 50+ jazyků | per-tenant volba bi-encoderu | per-org config: default lang + fallback chain | strukturální základ | 3-5 dní | žádný |
| 3 | **Per-language tokenizer chains** (CS Snowball, DE German2, FR French) | porter unicode61 je dnes EN-only; každý jazyk dostane vlastní stemmer | memee-team org-level tokenizer config | +2-5 nDCG per non-EN language | 1 týden | žádný |
| 4 | **Multi-language AST review** (Python → JS/TS → Go/Rust → Swift/Kotlin) | review.py jako prvotřídní gate ve všech stack | enterprise polyglot codebases (typický memee-team profil) | +25-30 nDCG na diff_review (měřeno na EN; estimate cross-lang) | 2-3 týdny / jazyk | R14 #4 ship |
| 5 | **Locale-aware PII redaction** (EU formáty: telefon, IBAN, BIC, RČ, EU IDs) | OSS user pracující s EU PII má redakci out-of-box | GDPR-ready audit; HIPAA EU varianta | enabler kategorie zákazníků | 1 týden | regulated prospect |
| 6 | **Multilingual eval harness rozšíření** (n=20 → n=120+; CS, DE, FR, ES, JA) | per-language regression gate | memee-team A/B per-tenant ranker | measurement substrate | 1-2 týdny | žádný |
| 7 | **Per-tenant language preferences** | OSS jednotlivec si nastaví fallback chain | memee-team org-level: default lang, fallback, model výběr | UX win | 3-5 dní | memee-team plugin hook |
| 8 | **Cross-language memory propagation** | Python pattern o timeoutech automaticky aplikuje na Go/Rust při tag overlap | enterprise s polyglot stackem dostane unified knowledge | strukturální (graph win) | 1-2 týdny | tag-canonicalization round |
| 9 | **Locale-aware briefing format** (DD.MM.YYYY, fr-FR čísla) | UX detail | enterprise look + feel professional | UX win | 2-3 dny | žádný |
| 10 | **Dashboard / CLI i18n** | OSS user může lokalizovat | enterprise demo v rodném jazyce | UX win | 2 týdny | first non-EN customer |

---

## Detailní popisy

### #1 Multilingual cross-encoder swap

**Co je dnes:** R14 #2 ship-rule passed s `cross-encoder/ms-marco-MiniLM-L-6-v2`,
což je **English-only** model trénovaný na MS MARCO. Multilingual_lite cluster
(n=20) v 207q harness se zlepšil jen o +0.0191 (n.s.) — protože model
nerozumí non-EN tokenům.

**Změna:** Swap na `cross-encoder/ms-marco-multilingual-MiniLM-L12-v2`
(50+ jazyků, vyrobeno přesně na tento use case, +200 MB wheel).
`MEMEE_RERANK_MODEL` je už env-tunable, takže je to změna defaultu, ne
architektury.

**Co přinese OSS:** CS / DE / FR / ES uživatel dostane stejnou kvalitu
ranku jako EN uživatel. Konkrétně: query "ošetři timeout u HTTP volání"
najde anti-pattern "Always set HTTP timeout" stejně jako "fix HTTP
timeout".

**Co přinese memee-team:** EU enterprise zákazníci (Memee má first
paying customer v Praze; další pipeline v DE/AT) dostávají out-of-the-
box rank ve svém jazyce. To je rozhodující bod při enterprise sales
demu — pokud demo selže na "ukažte mi search v němčině", deal padá.

**Dopad:** odhad +0.05 až +0.10 nDCG na multilingual_lite cluster.
Měření vyžaduje rozšíření harnessu (#6 v této tabulce).

---

### #2 Multilingual embedding backbone

**Co je dnes:** vector retriever používá `all-MiniLM-L6-v2` (384-dim,
EN-trained). Bi-encoder embeddingy non-EN textu mají horší kvalitu
clusteringu, což snižuje recall na vector retrieveru.

**Změna:** přidat `paraphrase-multilingual-MiniLM-L12-v2` (50+ jazyků,
podobná velikost) jako alternativu v `engine/embeddings.py`. Per-org
volba přes nový `MEMEE_EMBED_MODEL` env var nebo memee-team config.

**Co přinese OSS:** uživatel s českým/německým corpusem dostane lepší
vector retrieval. RRF má dva quality retriévery místo jednoho-a-půl.

**Co přinese memee-team:** per-org embeddingový model (jeden tenant
může běžet EN-optimized, jiný MultiLanguage). To je netriviální feature
v memee-team plugin systému, ale schéma už podporuje (memory.embedding
column je generic JSON list).

**Dopad:** strukturální. Sám o sobě malý nDCG win na EN; velký na
non-EN.

---

### #3 Per-language tokenizer chains

**Co je dnes:** R11 ship porter unicode61 (+0.0317 nDCG@10) — ale Snowball
porter stemmer je EN-only. Pro češtinu, němčinu, francouzštinu se chová
jako pouhý unicode61 (žádný stemming).

**Změna:** SQLite FTS5 podporuje `tokenize='porter french unicode61'` /
`tokenize='snowball german unicode61'` (přes Snowball stemmer wrapper).
Memee přidá:

- detekci jazyka memory.content při record-time (cheap heuristic; např.
  langdetect-Python knihovna jako optional dep `[multilang]`)
- per-language FTS5 virtual table (`memories_fts_cs`, `memories_fts_de`, ...)
- search routes do správné FTS5 tabulky podle detected query language

**Co přinese OSS:** český "validovat" matchne "validace", "validuje",
"validovaný" — stejný stemming win jako EN dostal v R11.

**Co přinese memee-team:** organizace s týmy v DE+EN (typický enterprise
profil) má každý tým efektivní FTS pro svůj jazyk. Org-level config:
"default language = de, fallback = en" znamená že DE memo se hledá
DE-stemmer, ale když uživatel pošle EN query, fallback na EN-stemmer
najde memo přes content overlap.

**Dopad:** odhad +2-5 nDCG@10 per non-EN cluster. Měřeno tehdy, když
expandujeme harness (#6).

---

### #4 Multi-language AST review

**Co je dnes:** R14 #4 ship-rule **failed** — review.py zůstává keyword-
based. Ale gamechanger #4 z `docs/gamechangers.md` plánuje neuro-symbolic
review s tree-sitter.

**Změna (multi-lang angle):** tree-sitter má language packs pro 50+
jazyků, každý ~2-5 MB. Roadmap:

- **v1**: Python (nejdřív, protože Memee uživatelé jsou Python-heavy)
- **v2**: + JS/TS (frontend-heavy týmy)
- **v3**: + Go, Rust (cloud-native + systems týmy)
- **v4**: + Swift, Kotlin (mobile týmy)

**Co přinese OSS:** Python user, který přidá `requests.get(url).json()`
do PR, dostane warning "missing timeout= kwarg" s 99% confidence místo
vágního "HTTP usage detected." Stejně tak JS user pro `fetch(url)`,
Go user pro `http.Get(url)`, atd.

**Co přinese memee-team:** typický enterprise customer má polyglot
codebase — backend Go + frontend TypeScript + mobile Swift/Kotlin. Pure
Memee OSS dnes pokrývá jen tu část kde regex-y matchnou Python-style
patterns. Multi-lang AST review znamená, že **každý jazyk v org dostává
team-specific anti-pattern enforcement** přes stejnou shared memory.

To je deep moat: žádný konkurent (CodeRabbit, Greptile, Codeium) nemá
shared memory + multi-language AST. Mají buď jedno, nebo druhé.

**Dopad:** +25-30 nDCG@10 na diff_review cluster (měřeno na EN; estimate
že non-EN languages s hotovou jazykovou podporou v tree-sitter dostanou
ten samý lift). Per-language wheel size cost: ~5 MB každý.

---

### #5 Locale-aware PII redaction

**Co je dnes:** R12 P1 audit identifikoval chybějící PII redakci jako
gating item pro regulated industries. Plán v `docs/gamechangers.md` #6
zmiňoval univerzální regex pravidla — ale to je US-centric.

**Změna:** rozšíření redakčních pravidel o **lokálně specifické formáty**:

- **EU**:
  - IBAN (`[A-Z]{2}\d{2}[A-Z0-9]{11,30}`)
  - BIC / SWIFT (`[A-Z]{6}[A-Z0-9]{2}[A-Z0-9]{3}?`)
  - DE Steuer-ID (11 čísel)
  - FR INSEE / NIR (15 znaků)
  - CZ rodné číslo (`\d{6}/\d{3,4}`)
  - PL PESEL (11 čísel)
  - VAT ID per země
- **US**: SSN (`\d{3}-\d{2}-\d{4}`), EIN
- **UK**: NHS number, NI number
- **JP**: My Number
- **Telefonní formáty**: `+420`, `+49`, `+33`, `+44`, `+1`, `+81`, atd.

Per-org / per-scope policy: `redaction_rules: ["us", "eu", "cz"]` v
memee-team config; OSS má všechny default-on.

**Co přinese OSS:** český vývojář pracující s českými klienty má redakci
RČ, telefon, IBAN out-of-box. Žádný PII leak do embeddingu nebo
SearchEvent.

**Co přinese memee-team:** GDPR DPA conversation se posune z "pošlete nám
požadavky" na "tady je auditní evidence, podpis na ulici". HIPAA-EU
(EU+US healthcare overlap, e.g. transatlantic clinical trials) má precedent.

**Dopad:** žádný nDCG, ale **enabler kategorie zákazníků**. Ten samý
dopad jako gamechanger #6 z gamechangers.md, jen rozšířený o EU/lokální
specifika.

---

### #6 Multilingual eval harness rozšíření

**Co je dnes:** 207-query × 255-memory harness má `multilingual_lite`
cluster s n=20 (10 queries v CS, 10 v DE). To je málo na statistickou
sílu — minimum-detectable-effect je ±0.10 nDCG.

**Změna:** rozšíření na n=120+:

- 30 CS queries (rodný jazyk autora, nejlépe pokryto)
- 25 DE queries (memee.eu pipeline)
- 20 FR queries
- 20 ES queries
- 15 PL queries (regional EU)
- 10 JA queries (asijská market opportunity)

Plus per-language gold set — ne jen překlad EN queries, ale skutečně
jazykově-specifické tasks (např. "ošetři výjimku v Pythonu" má
sub-cluster, který testuje stemming "výjimka/výjimky/výjimkou").

**Co přinese OSS:** každá ranker change má per-language regression gate.
Když někdo navrhne tokenizer change, harness řekne přesně, který jazyk
to rozbije.

**Co přinese memee-team:** memee-team customer s českou codebase má
důvěru že upgrade Memee nezhorší jeho ranking. Per-tenant regression
gate je v dokumentaci: "ano, testujeme i česky."

**Dopad:** measurement substrate. Sám o sobě 0 nDCG win, ale **bez
něho nemůžeme ship #1, #2, #3 čistě**.

---

### #7 Per-tenant language preferences

**Co je dnes:** žádná language config existuje. Search vždy běží
shodně bez ohledu na tenant.

**Změna:** nový plugin hook `memee.plugins.register("language_config", impl)`,
který memee-team plnohodnotně implementuje. OSS má no-op default.

```python
# memee-team registers:
def language_config(session, user_id):
    return {
        "primary": "cs",
        "fallback_chain": ["cs", "en"],
        "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
        "rerank_model": "cross-encoder/ms-marco-multilingual-MiniLM-L12-v2",
        "stemmer": "snowball-czech",
    }
```

`search_memories` čte tento config a routes do správného FTS5 / vector
modelu / cross-encoderu.

**Co přinese OSS:** jednotlivec může v `~/.memee/config.toml` napsat:
```toml
[language]
primary = "cs"
fallback_chain = ["cs", "en"]
```
a Memee se chová podle toho.

**Co přinese memee-team:** org-level config přes admin UI. Multi-tenant
organizace s pobočkami v DE + CZ + UK má per-team config bez kódu.

**Dopad:** UX win. Nutné pro Day-One enterprise demo.

---

### #8 Cross-language memory propagation

**Co je dnes:** R7 propagation engine kopíruje paterny napříč projekty
podle tag overlap. Ale tagy jsou jazykově specifické: "timeout" v Python
projektu, "Zeitlimit" v DE projektu — žádné propagation.

**Změna:** tag canonicalization round v dream cycle. Když dream
detekuje, že tag `timeout` (EN) a `časový limit` (CS) odkazují na
stejný koncept (přes embedding similarity), zapíše to jako alias do
nové tabulky `tag_alias`. Propagation pak používá kanonický tag pro
cross-projekt match.

```python
# new table:
class TagAlias(Base):
    canonical_tag = Column(String(100), primary_key=True)
    alias_tag = Column(String(100), primary_key=True)
    language = Column(String(10))
    confidence = Column(Float)
```

**Co přinese OSS:** Python tým s tagem `timeout` a Go tým s tagem
`časový-limit` mají přístup k stejnému anti-pattern, i když ho nikdo
ručně netaggoval cross-language.

**Co přinese memee-team:** typická enterprise organizace má desítky
projektů v různých jazycích programovacích i přirozených. Cross-
language propagation odstraní ručně udržovaný "tag mapping" který je
jinak compliance burden.

**Dopad:** strukturální. Memory graph se rozšíří o ~30-50 % efektivních
edges při steady state na multi-language org.

---

### #9 Locale-aware briefing format

**Co je dnes:** briefing renderuje `2026-04-25` (ISO) napříč všemi
locale. Čísla v EN formátu (1,000,000.00).

**Změna:** per-locale formatter:

| locale | datum | čísla | confidence |
|---|---|---|---|
| en-US | 04/25/2026 | 1,000,000.00 | 94% |
| en-GB | 25/04/2026 | 1,000,000.00 | 94% |
| cs-CZ | 25. 4. 2026 | 1 000 000,00 | 94 % |
| de-DE | 25.04.2026 | 1.000.000,00 | 94 % |
| fr-FR | 25/04/2026 | 1 000 000,00 | 94 % |

**Co přinese OSS:** professional look pro non-EN uživatele.

**Co přinese memee-team:** enterprise demo "vypadá jako naše firemní
dokumenty" je rozdíl mezi "vyzkoušíme to" a "podepíšeme."

**Dopad:** UX detail. Trivial implementace (Python `babel` knihovna).

---

### #10 Dashboard / CLI i18n

**Co je dnes:** všechny stringy v dashboardu i CLI jsou hardcoded EN.

**Změna:** standardní gettext-style i18n. Přeložit:

- Dashboard UI strings (~150 stringů)
- CLI help texts (~80 stringů)
- Error messages (~50 stringů)

První bundle: CS + DE + FR. Další jazyky podle customer demand.

**Co přinese OSS:** non-EN uživatelé vidí "Hledat" místo "Search" v
dashboardu. Profesionální feel.

**Co přinese memee-team:** **enterprise demo v rodném jazyce zákazníka
je deal-closer**. Měřeno v B2B SaaS prodeji obecně: lokalizovaná
demo má ~20 % vyšší conversion v EU.

**Dopad:** UX win, deal-closer pro non-EN enterprise.

---

## Co je multilingual + memee-team **úzce provázané**

Tabulka průniků: které řešení specificky umožní memee-team prodej do
EU/regulovaných odvětví, které Memee OSS dnes nemůže obsluhovat.

| Trh / segment | Klíčová řešení | Aktuální blocker |
|---|---|---|
| EU enterprise (DE/FR/CZ/PL) | #1 + #2 + #3 + #7 + #10 | Search kvalita v EN-trained modelech je horší → demo failu |
| Healthcare EU (HIPAA + EU GDPR) | #5 + #6 (z gamechangers.md) | Žádná lokálně specifická PII redakce |
| Regulated finance EU (SOX + GDPR) | #5 + #2 (canon ledger z gamechangers.md) | Žádný audit trail s lokálními ID formáty |
| Enterprise polyglot codebase | #4 (multi-lang AST) + #8 | Review.py jen Python-style; cross-lang propagation neexistuje |
| Government / public sector EU | #5 + #2 + #6 (privacy z gamechangers.md) | Žádná lokální PII compliance |

---

## Sequencing — co první (multi-lang fokus)

Pokud bych měl měsíc na multi-lang push:

### Týden 1 — #6 Harness rozšíření (measurement first)
Bez per-language test setu nemůžeme nic shipnout čistě. Začít expanze
multilingual_lite cluster z n=20 na n=120+ napříč 6 jazyků.

### Týden 2 — #1 + #2 Multilingual modely
Swap cross-encoder na multilingual variant. Přidat
`paraphrase-multilingual-MiniLM-L12-v2` jako alternativní embedding.
Měřit na rozšířeném harnessu z týdne 1.

### Týden 3 — #3 Per-language tokenizers
Rozšířit FTS5 setup o per-language stemmery (CS, DE, FR). Per-language
FTS5 virtual tables. Routing logika.

### Týden 4 — #7 + #9 Per-tenant config + locale UX
Plugin hook + locale formatter. To otevře memee-team admin UI.

Pak Q2:
- #5 Locale-aware PII redakce (po prvním regulated EU prospektu)
- #4 Multi-language AST review (po hlavním #4 ship z gamechangers.md)
- #10 Dashboard i18n (po prvním non-EN paying enterprise)
- #8 Cross-language propagation (po dvou cyklech tag-canonicalization
  dream output dat)

---

## Honest framing

- **Číselné odhady jsou estimates**, ne měřené hodnoty. Aktuální
  harness má `multilingual_lite` n=20 — minimum detectable effect je
  ±0.10 nDCG, takže jakékoliv tvrzení "+0.05 lift" je pod statistickou
  hladinou. **Tým 1 (#6 harness expansion)** je předpoklad pro všechna
  ostatní měření.
- **EU customer pipeline je krátký.** Memee má první paying customer
  v Praze; další pipeline je v DE/AT. To je gating signal pro #5 a #10
  — ne plánovat speculatively, čekat na signed deal.
- **memee-team plugin systém už nese 80 % schématu**, které tato řešení
  potřebují. `engine/plugins.py` má `register/get/call` API; přidat
  nové hooks je 1-2 hodiny code.
- **Aspoň jedna položka selže.** Když jmenujete 10 řešení, jedno
  nefunguje jak očekáváno. R14 #1 (field-aware BM25) byl honest negative;
  v tomto seznamu nejvíc rizikové: **#3 per-language tokenizer chains**
  — Snowball stemmery pro CS jsou méně odzkoušené než EN porter.

Last updated: 2026-04-25.
