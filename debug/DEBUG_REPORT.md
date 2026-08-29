# 🔍 Debug Report — `SBZ-EDU/dual-diagnosis-rag`**Date:** 2026-08-29 · **Repo state audited:** master @ `e7c2d9a` (24 commits)
**Scope:** full repo — Python RAG app, Gradio UI, risk engine, PubMed watcher, Cloudflare Worker, notebook, scripts, data/index artifacts.
**Method:** static review + live execution (index rebuilt with the real multilingual MiniLM model, Gradio app served and queried through its REST API, PubMed E-utilities called live, worker syntax-checked).

The fixed project is in `~/dual-diagnosis-rag` (all changes applied and verified), and a single git patch with everything is saved as `~/dual-diagnosis-rag-debug.patch`.

---

## Executive summary

The project is well-structured (fallback-first generator, pseudonymised patient IDs, deny-by-default Firestore rules), but it shipped with **two bugs that silently gutted the product**:

1. **The deployed RAG answered from ~4% of its knowledge base.** The committed vector index contained only the 45 protocol chunks; the 950+ article chunks and 10 clinical guidelines added in later commits were **never indexed** and were invisible at runtime — the app skips rebuilding whenever index files merely *exist*.
2. **A fresh `pip install -r requirements.txt` produces a broken app today.** Unpinned `fastapi`/`starlette` resolve to versions incompatible with `gradio==4.44.1` (every request 500s with `TypeError: unhashable type: 'dict'`); on Python 3.13 `torch==2.4.1` has no wheels at all.

Both are fixed, along with 9 more issues found along the way (index pollution, embedding dilution, a dead cache in the Cloudflare worker, per-query disk I/O, and others). All 22 regression tests pass and the fixed app was verified live end-to-end.

---

## 🔴 Critical bugs (fixed)

### 1. Stale vector index — 96% of the knowledge base was invisible

**Where:** `app.py:ensure_index()`, `index/chunks.json`, `index/vectors.npz`
**Symptom:** no error — the app runs fine but retrieves only from `protocol.md`.

- Committed index: **45 chunks, all `type: protocol`**.
- Actual data on disk: 1005 valid chunks (protocol + 972 article + 10 guideline + …).
- Root cause: `ensure_index()` checked `store.exists()` — file *existence*, not *freshness*. The README even relies on a pre-built index "so the Space starts fast", which guaranteed the staleness.
- Consequence: every question about the 200-paper corpus, WHO/UNODC/Iran-MOH guidelines, or PubMed evidence could only be answered if the protocol happened to mention it.

**Fix:**
- `rag/store.py`: new `data_manifest()` (SHA-256 over source path+mtime+size) + `save_manifest()` + `manifest_matches()`.
- `app.py`: `ensure_index()` now rebuilds when the index is missing **or stale** relative to the data.
- `scripts/build_index.py`: writes `index/manifest.json` after building.
- The index itself was rebuilt and the fresh 1005-chunk index is committed in the fixed tree (64 KB → 1.4 MB `vectors.npz`, still small).

### 2. Fresh installs are broken (dependency hell)

**Where:** `requirements.txt`
**Symptom (reproduced in a clean venv):**

```
ImportError: cannot import name 'HfFolder' from 'huggingface_hub'   # hub ≥ 1.0
TypeError: unhashable type: 'dict'                                   # starlette ≥ 0.47
ValueError: When localhost is not accessible, a shareable link must be created...
```

- `gradio==4.44.1` declares `fastapi<1.0` and `huggingface-hub>=0.19.3` — ranges that admit versions known to break it:
  - **starlette ≥ 0.47** (pulled by any recent `fastapi`) breaks gradio's `TemplateResponse` → every page/API request 500s, and `launch()` then aborts because its localhost health check fails. Unpinned in `requirements.txt`.
  - **huggingface_hub ≥ 1.0** removed `HfFolder`, which gradio 4.44 imports. Only accidentally avoided when `transformers==4.44.2`'s own `hub<1.0` constraint wins resolution — fragile, and the *notebook* installed `huggingface_hub<2`, which does allow 1.x.
- `torch==2.4.1` has **no wheels for Python 3.13** → install fails outright there.

**Fix (requirements.txt):**

```
fastapi<0.116            # keeps starlette<0.42 — required by gradio 4.44.1
huggingface_hub>=0.28,<1.0
torch==2.4.1 ; python_version < "3.13"
torch>=2.6,<3 ; python_version >= "3.13"
```

Verified: with `fastapi 0.115.12 / starlette 0.46.2 / hub 0.36.2` the app boots, serves 200s, and the API works.

### 3. Index pollution — metadata files ingested as "knowledge"

**Where:** `rag/data.py:load_documents()`

Everything in `data/articles/` that isn't a `.gitkeep` was treated as a knowledge source:

- `article_validation_report.json`, `extended_corpora_report.json`, `open_access_pdf_report.json` → raw **JSON dump text** was chunked and embedded as `type: article` (internal counters like `"total": 20, "valid": 20 …` became retrievable "evidence").
- `open_access_pdf_manifest.jsonl` → **199 title-only chunks** such as `"Schizophrenia"` / `"Opioid use disorder"` — pure bibliography entries that outrank real content and waste top-k slots.
- 15 chunks under 40 chars (bare markdown headers, etc.).

**Fix:** explicit exclusion of `*_report.json` / `*_manifest.jsonl`, a `MIN_CHUNK_LEN=40` floor, and header-only sections are merged into the following chunk instead of standing alone. Result: **0 junk chunks** (verified programmatically).

### 4. Embedding dilution by URLs — retrieval quality halved

**Where:** `rag/data.py` record formatting (and pre-existing in the original formatter)

The URL/DOI was embedded **inside the chunk text** *and* stored in the `source` field. Long URL token sequences drag the mean-pooled MiniLM vector far from the query. Measured on a real query:

| chunk text variant | cosine similarity to «متادون برای وابستگی به مواد افیونی» |
|---|---|
| title + org + **URL** | **0.169** |
| title + org (no URL) | **0.366** |

**Impact:** the Iran-MOH **methadone protocol** ranked **#225** for a methadone query. **Fix:** keep the URL only in `source` (it's what the UI cites anyway). After rebuilding, the methadone protocol ranks **#5 (0.405)** and the SBMU addiction-treatment guideline is **#1 (0.521)**. A live API call for "درمان وابستگی به مواد افیونی با متادون چیست؟" now returns the two Iranian guidelines + protocol + two relevant papers as its five sources.

### 5. Cloudflare Worker: cache written but never read (dead code)

**Where:** `cloudflare/src/index.js` `/api/chat`

```js
const cached = await env.DB.prepare("SELECT answer,source FROM rag_cache WHERE cache_key=? AND created_at>datetime('now','-7 days')")...first();
// cached is never referenced again — the LLM is called anyway
```

Every chat request paid for `hash()` + a D1 read, then **re-ran the full LLM pipeline and overwrote the cache**. The 7-day TTL query made the intent obvious.

**Fix:** early return of `cached` with `source: "<provider> (cache)"`, `cached: true`, plus an `api_usage` row. Syntax re-verified with `node --check`.

---

## 🟠 Medium bugs (fixed)

6. **Index reloaded from disk on every query** — `retriever.retrieve()` called `store.load()`, which re-parsed `chunks.json` (JSON) and `vectors.npz` on **every** request. Now cached in memory with mtime-based invalidation. Retrieval latency: **24 ms** after warm-up.
7. **`scripts/ingest.py` couldn't ingest guidelines** — `--kind` choices omitted `guidelines` although `config.SOURCES` defines that folder. Added.
8. **`scripts/` had no `__init__.py`** — `from scripts import build_index` in `app.py` only worked via implicit namespace packages (cwd-dependent). Added.
9. **`datetime.utcnow()` deprecated** (Python 3.12+) in `pipeline.add_feedback` → timezone-aware `datetime.now(timezone.utc)`. Feedback records are also now ingested as *readable text* («سؤال: … / پاسخ: … / امتیاز: …») instead of raw JSON blobs.
10. **PubMed watcher query too broad** — live run returned off-topic hits (e.g. *"Antihypertensive treatment and adverse psychiatric reactions…"*). Query now uses `[tiab]` field tags + `sort=relevance`. Re-ran live: 20 on-topic results.
11. **Notebook dependency pins** — cell 4 installed unpinned `gradio` (today → 5.x/6.x, API-incompatible with this `app.py`) and `huggingface_hub<2` (allows 1.x). Pinned to `gradio==4.44.1`, `fastapi<0.116`, `huggingface_hub<1.0` to match the app.

---

## 🟡 Notes & recommendations (not changed)

- **`firebase/web-config.js` contains a real Firebase API key / project ID.** Web keys are public by design and `firestore.rules` correctly deny unauthenticated access (verified: default `allow read, write: if false`, clinician role required elsewhere) — but the key should still get HTTP-referrer restrictions in the console, and `PATIENT_ID_SALT` must be a strong secret or pseudonymisation is worthless.
- **Worker endpoints are unauthenticated with `access-control-allow-origin: *`** — `/api/chat` burns Workers-AI/HF tokens for anyone who finds the URL. Consider Cloudflare Access / a shared token + rate limiting.
- **Worker `wrangler.toml` commits `account_id` and D1 `database_id`s** — identifiers, not secrets, but some teams prefer keeping them out of public repos.
- **Risk-engine parity:** the Python `assess()` honours red `flags` (`suicidal_plan`, `delirium`, …); the worker's `/api/risk` only checks `suicide==4 || withdrawal==4`. Not a crash, but the two surfaces can disagree on severity.
- **`USE_GENERATOR` defaults to `1`** in `config.py` (`.env.example` says `0`): a cold Space downloads Qwen2.5-0.5B before serving. If you don't want that, default it to `0` in `config.py`.
- **`wandb.log({"method": ...})`** with a string value — tested, wandb accepts it; no fix needed.
- **Privacy:** free-text feedback (potential PHI) lands in `data/feedback/` and is re-ingested into the RAG index on rebuild. That's by design ("بازخورد کاربران" is a listed source), but worth an explicit consent note in the UI.

---

## ✅ Verification

| Check | Result |
|---|---|
| `python -m scripts.build_index` (real MiniLM model) | 1005 chunks, manifest written |
| Regression suite (22 checks: data loading, chunking, exclusion, risk engine edges incl. clamping & max score 76, store cache + staleness, search edge cases, retrieval relevance, feedback format) | **22/22 PASS** |
| Gradio app boots (`USE_GENERATOR=0`) | 200 on `/`, no errors |
| Live `/answer` API (Persian clinical questions) | correct, sources include guidelines + protocol + papers |
| Live `/risk` API (suicide=4) | level «بحرانی», correct action text |
| PubMed watcher (live E-utilities call) | 20 relevant articles fetched |
| `node --check` on worker + page | OK |
| Before/after retrieval for methadone query | rank 225 → rank 5 |

## 📦 How to apply

Everything is already applied in `~/dual-diagnosis-rag` in this workspace. To move it into your own checkout:

```bash
cd dual-diagnosis-rag
git apply dual-diagnosis-rag-debug.patch
python -m scripts.build_index   # or rely on the new auto-rebuild guard
python app.py
```

The patch also contains the rebuilt `index/` (1005 chunks + `manifest.json`) so the Space starts with the full knowledge base immediately.

---

# 📎 Addendum (session 2) — protocol site, Telegram bot, Cloudflare deployment

## 1. Second repo audited: `SBZ-EDU/dual-diagnosis-protocol`

Static Persian clinical site (HTML/CSS, no JS). Structure is clean (valid RTL/UTF-8/viewport, balanced tags, no broken anchors, correct print CSS for the patient sheet). Fixed:

| # | Issue | Fix |
|---|---|---|
| A1 | Google Fonts loaded as a **render-blocking stylesheet**, while README claimed "no external runtime dependency" — stalls first paint when `fonts.googleapis.com` is slow/unreachable (common in Iran, this project's audience) | async `media="print" onload` pattern + `<noscript>` fallback; falls back to Tahoma instantly; README wording corrected |
| A2 | No Open Graph / Twitter card meta — bare Telegram link previews | added og:title/description/type + summary card + SVG favicon |
| A3 | No cross-links between the two repos and the bot | footer now links the Telegram assistant and the RAG engine |

Both fixes pushed to GitHub (`8111746..e84fdcc`).

## 2. Telegram bot + channel auto-posting (`dual-diagnosis-rag`)

New entrypoints `telegram_bot.py` + `telegram_posts.py` (see README section «ربات تلگرام و ارسال خودکار به کانال»):
RAG Q&A + interactive `/risk` (inline buttons, stale-click guard), daily rotating psychoeducation tips (14, sourced), weekly PubMed digest, admin commands, anti-flood, no message-content logging, per-message medical disclaimers. Token lives only in gitignored `.env`.

## 3. Cloudflare Worker — deployed & fixed live

Worker `dual-diagnosis-clinical-hub` (workers.dev) with 2 D1 databases.

| # | Issue found on live deployment | Fix |
|---|---|---|
| C1 | **Dead cache**: `/api/chat` fetched `rag_cache` but always re-ran the LLM | early return of cached answer — verified live: repeat call **1967ms → 69ms**, `cached: true` |
| C2 | **Polluted evidence corpus**: live `research_papers` (199 rows) included cancer statistics, ESC heart-failure guidelines, osteoporosis etc., injected as "evidence" into clinical answers (observed the LLM giving a wrong clozapine recommendation because of it) | new `scripts/build_d1_seed.py` with transparent relevance filter (strong/weak phrase scoring, title-first, empty-abstract handling) → **190 topical papers, 9 dropped**; remote D1 re-seeded |
| C3 | Persian queries fell back to generic top-cited papers (expansion map lacked clozapine/schizophrenia/methadone/…) | expanded Persian→English clinical terms + **title-first two-pass search**; live answer now cites the Lancet Schizophrenia seminar, MOUD comparative-effectiveness study and APA guideline |

Commits: `1a9e0d4` (RAG fixes), `8bea758` (Telegram bot), `2d7138d` (Cloudflare corpus/retrieval) — all pushed to master.

## ⚠️ Secrets exposed in chat (action required)

The following were pasted into the conversation and should be **rotated/revoked** now that they've served their purpose:
1. **Telegram bot token** (`8594493505:AAF…`) → @BotFather → /mybots → API Token → Revoke, then update `dual-diagnosis-rag/.env`
2. **GitHub PAT** (`ghp_…`) → GitHub → Developer settings → Tokens → delete/rotate
3. **Cloudflare API token** (`cfat_…`) → dash.cloudflare.com → API tokens → roll
4. R2 S3 secret (was never used)

None were committed to any repo (verified: secret scan clean, `.env` gitignored).

---

# 📎 Addendum (session 3) — protocol site ↔ Cloudflare hub integration

**Goal:** bring the full protocol content into the deployed clinical hub and make both web properties answer questions in Persian.

| Change | Where | Verified |
|---|---|---|
| New **`/protocol`** route serving the complete protocol page (inline CSS, RTL) built from `SBZ-EDU/dual-diagnosis-protocol` | Cloudflare worker (`protocol_page.js`) | live: 200, 69.7 KB, all sections present |
| **Persian chat widget** (floating panel, vanilla JS, XSS-safe `textContent` rendering, graceful offline state, hidden in print) embedded in the `/protocol` page (same-origin `/api/chat`) | worker | live chat call answers in Persian |
| Same widget added to the **static protocol site** (GitHub repo), calling the worker via absolute URL — CORS preflight `OPTIONS` + `POST` verified from arbitrary origins | `dual-diagnosis-protocol` | structure checks pass; CORS 200 |
| Hub main page now links to «📖 پروتکل کامل درمان» | worker `page.js` | live |
| System-prompt hardening: the assistant must never present itself as a doctor (it used to say «به عنوان یک پزشک…») | worker `/api/chat` | live: claim absent, identifies as educational assistant |

Commits: `dual-diagnosis-rag@4c6568c`, `dual-diagnosis-protocol@ce86f11`.

Note on the sandbox: `.git/config` is not persisted across sessions (by design), so git identity and the remote URL must be re-set per session; local history was re-synced to the remote head before each push to avoid stale-base rejections.

---

# 📎 Addendum (session 4) — space cleanup, bot bottom menu, full-Persian answers

**Space** (workspace + repo):
- removed the obsolete 524 KB debug patch (repos are pushed; patch was redundant)
- index storage compacted: **float16 vectors + no-indent JSON → 1.93 MB → 1.21 MB** (max cosine drift 6e-5; retrieval verified unchanged, manifest still valid)
- `git gc --aggressive` on both repos; workspace now ~5.8 MB / 260 files (well under snapshot limits); tracked repo content = 2.4 MB

**Telegram bot** (commit `92105dc`, live):
- 🎛 persistent **bottom reply-keyboard**: «📈 پایش خطر / 🎓 نکته امروز / ❓ راهنما / 📚 درباره», with Farsi input placeholder; button presses route to the matching feature
- 📋 command menu registered via `setMyCommands` (users see the 7 main commands; admins get an admin-scoped set) — verified live (`200 OK`)
- new `/tip` — today's evidence-based psychoeducation post (same content as the channel auto-post)
- **All-Persian answers**: source types now show as مقاله/پروتکل/راهنمای بالینی/سابقه بیمار/بازخورد in the bot, web app and extractive answers; mostly-English snippets are marked «منبع انگلیسی»

All 9 regression checks passed; bot relaunched and polling.

---

# 📎 Addendum (session 5) — the robot moved into the protocol repo (main part)

**Architecture now:** `dual-diagnosis-protocol` (main part: site + robot + risk engine + tips + protocol text) ⟷ `dual-diagnosis-rag` + Cloudflare worker (AI part: semantic RAG + LLM answers).

| Change | Detail |
|---|---|
| **`bot/` added to the protocol repo** | standalone robot: only `python-telegram-bot` (no torch), boots in ~2s. Bottom menu (پایش خطر/نکته امروز/بخش‌های پروتکل/راهنما), interactive risk quiz — **engine parity with the AI repo verified on 6 cases**, 14 sourced tips, protocol-section browser (inline buttons), Persian Q&A |
| **Q&A delegation** | questions go to the deployed worker `/api/chat` (AI part); offline fallback searches `bot/protocol.md` locally — token-based matching (no substring false-positives like «بیت» inside «نسبت»), title-boost ×10, window-around-match excerpts; off-topic → no match |
| **Fallback search tests** | 10/10 pass (خانواده→همراه, کلوزاپین→الگوریتم دارویی, سطوح درمانی/تماس‌ها/برنامه فوری→title sections, آشپزی/بیت‌کوین→none) |
| **Site quick-reply chips** | chat widget gains 4 one-tap questions mirroring the bot buttons; worker `/protocol` regenerated from the same single-source widget (API rewritten to same-origin) and redeployed — verified live |
| **Bot switchover** | rag-repo bot stopped; the protocol-repo robot now serves t.me/AI_Aiddiction_Assistant_bot (getMe/setMyCommands/polling all 200) |
| Commits | protocol `ce86f11..e4ba1e0`, rag `92105dc..0251ba0` |

Token conflicts: one bot token supports one poller — both repos' bots are functional, but only one may run at a time (documented in `bot/README.md`).
