## ▶ START HERE — handover (last updated 2026-10-08)

**Phases 0–8 are built.** The engine now runs as a guided flow with a production crew. Code:
`ugc-growth-engine/`. Full how-to: `ugc-growth-engine/README.md` §0 "Quick start".

**Start the engine:**
`cd ugc-growth-engine && .venv/bin/streamlit run app.py --server.port 8501`, then open http://localhost:8501
(the **Engine** page).
- Restart Streamlit after every code change; hot reload is off on purpose.
- Check UI changes in the browser before saying they're done.

**The owner's flow (keep it this way; the owner wants one simple, systematic dashboard):**
1. Choose or create an app. Delete is the trash icon, with a type-the-name confirmation.
2. **Go →** runs `cli.py go <app>`: research → understanding (shown as chips) → 10 scripts.
3. The owner approves the scripts. The credit ceiling is the estimate + 15%.
4. Claude records the voice lines through the **Magnific MCP**. The REST API can't use the cast Indian voices.
   Run `cli.py batch voice-jobs N`, record each line with `audio_tts` (eleven_v3, stability 0.45, speed 1.05,
   the voiceId from the job), then `crew import <run> <req> "<url>" --credits N --creation "<url>"`.
5. `cli.py batch run N` produces the videos one by one. Visual Magnific jobs go through the API (`MAGNIFIC_API_KEY`),
   and free Pexels stock is tried first.
6. Production board: approve, send back with a note, download one video or download all as a zip.

**Rules the owner set:**
- **Magnific spend:** show the jobs and a credit total, wait for a yes, and finish videos from assets already paid
  for before buying more.
- **Cost policy (2026-09-30):** Magnific is OFF by default: premium voice, AI stills, hook clips and character modes are
  disabled in `config/production.yaml`; videos use free stock, real screenshots and edge-tts. QC revision rounds may not
  buy assets (`allow_paid_revisions: false`), the approval ceiling has no margin, and the monthly engine cap is 600
  credits. A paid run needs a yes on the exact job list; a batch approval never covers extra jobs.
- **Long runs:** show live progress; never run anything silently or two runs at once (one crew per app, enforced
  by a lock).
- **Generic engine:** it must work for any future app from its brief alone, with no per-app hand setup.
- **App screens:** only real screenshots; never generate fake app UI. Engine guards enforce this.
- **Text looks:** every video gets its own text look (fonts, colours, layouts). No AI-generated label on the video
  (`SHOW_AI_LABEL=0`).

**Current state (Cash Calculator, app id `cashcalc`, Play Store `com.appfactory.cashcalculator`):**
- **Batch #1 is done:** 8 of 8 videos, awaiting owner approval; 4,126 of 4,312 approved Magnific credits used.
  Copies are in `~/Downloads/ugc_pilot_cashcalc/batch1/`.
- **Free fixes still to do:**
  - #373 opens on a black text card; needs a real hook shot.
  - #374 shows US dollars in the envelope shot; needs a rupee stock shot.
  - #372 hook text is squeezed.
  - "Total in words" isn't shown on any real app screen (#370, #375, #376).
- **Not AI-reviewed:** #372–#376, because the Gemini daily cap (₹500) was hit. Re-review once it resets.
- **Dashboard:** the modern theme, a Download icon on each card, the "Download all" zip and delete app are live.
  They were checked by restart plus a headless render (0 errors, 9 download buttons). The zip contents haven't
  been opened and checked yet.
- **Rotate secrets:** the owner pasted the Magnific API key and webhook secret in chat.

**Progress log, 2026-10-08 (what was done after the Cash Calculator `cashcalc` pilot above):**
- **Languages are decided per app, not engine-wide.**
  - `cashcalc`, `cashbook_us` (US Cash Calculator), `motorinsurance` and `upisoundbox` briefs are English only
    (`languages: ["en"]`, `languages_strict: true`). The owner saw Hindi scripts and was angry, so check an app's
    language before writing or approving scripts.
  - `motorins_in` is Hindi, India market, by the owner's explicit request. Run it with `ENGINE_MARKET=IN`.
  - Default market is TIER1 (US/UK/CA/AU, English). 52 non-English draft scripts were rejected.
- **Apps in the engine now:** `cashcalc`, `cashbook_us`, `motorinsurance`, `upisoundbox`, `motorins_in`
  (Play Store `com.insurance.calculator.motor`) and `clockvibe` (Play Store `com.clockvibe.app`, English, Tier-1).
  Briefs are in `data/sample_briefs/`.
- **3 English lip-sync videos for `cashbook_us`** (#390 QC 90, #391 QC 67, #394 QC 87) are in
  `~/Downloads/CashCalculator_Lipsync/`. The presenter is Marcus (`marcus_presenter`).
  - **They cost about 8,150 real credits, almost twice the owner's 4,200 ceiling.**
  - The engine's price estimate for lip-sync (about 180 credits/s) is roughly 2.5x too low, because Magnific's API
    does not report real costs.
  - **Before any paid run, check the real balance in the Magnific account menu after the first video.** Do not trust
    the engine's logged total. Balance afterwards: about 1,359 of 20,000 credits.
  - The engine only plans lip-sync when every line has a premium voice and the video fits the cap. The Magnific MCP
    was not connected, so a scratchpad script ordered voices, the presenter frame and the talking clip through the
    REST API and then resumed the crew.
  - Keep hook and CTA lines to 6–7 words (billed per second).
- **Magnific stock is now the first footage source** (`engine/visuals.py`). It uses the licensed stock library at
  `/v1/videos` with `filters[aspect_ratio][]=9:16`, no AI-generated clips, and costs no credits. Search uses 3–4 key
  words from the scene. Downloads are hundreds of MB, so each clip is re-encoded (15 s, 1080p, 30 fps) by `_shrink`.
  Pexels is the fallback.
- **Batches made with no AI credits** (faceless, free edge-tts voice, Magnific stock):
  - `cashbook_us`: 20 English videos, batches #5 and #6, in `~/Downloads/CashCalculator_English_Stock/`.
    4 of 20 passed QC (80): #409, #411, #413, #418.
  - `motorins_in`: 20 Hindi videos, batches #7 and #8, in `~/Downloads/MotorInsurance_Hindi_Stock/`.
    2 of 20 passed: #436, #440.
  - `clockvibe`: 20 English faceless Tier-1 videos (batches #9 and #10), done, in `~/Downloads/ClockVibe_English_Stock/`.
    4 of 20 passed QC (80): #447, #449, #459, #463. #461 scored 7 and needs a re-cut or should be dropped.
  - Most videos score 42–70 because stock footage often does not match the scene. Watch before publishing.
- **Engine fixes made:** `render.py` `cover_background` no longer crashes when the first scene is a motion graphic.
  A render watchdog is needed because a Remotion render can hang at 0% CPU (it kills renders over 12 min).
  Reset runs stuck in `running` to `failed`, then run `cli.py batch run N` again to retry only the unfinished ones.
- **Config:** `config/production.yaml` was raised for the lip-sync run and then restored to the original (Magnific
  OFF, `monthly_credit_cap: 600`). Do not raise it again without the owner's yes on an exact job list.
- **Owner preferences seen this session:** no discussion, just do it; videos copied into `~/Downloads` as they finish;
  never claim a result without checking the real number.

**Gotchas:**
- A Remotion render hung at 0% CPU for 21 minutes and a batch stalled; kill the `render.mjs` process and the batch moves on.
- Store screenshots are marketing images with their own titles, so headlines are skipped on screenshot scenes.
- A background browser tab won't load `<video>`, so test video playback in the foreground tab.
- JSON columns: deep-copy `run.plan` before editing it, or the change won't save.
- `pkill -INT` doesn't stop `batch run`; use TERM, or TaskStop on the background task.

---

> **Instructions for Claude Code:** Read this whole file before writing any code. Build phase by phase (Phase 0 → Phase 8). After each phase, run that phase's acceptance checks, fix anything broken, and only then continue. Use only free services and open-source tools. If a free service fails, use the listed fallback. Ask me before changing the tech stack or skipping a deliverable.

---

## 1. Hackathon question and goal

**Question:** Can AI-generated UGC content and social become a repeatable, low-cost growth lever for our apps?

**Goal:** Build a working AI-powered UGC & Social Growth Engine for the Indian market that generates, tests, and optimizes social content for our apps at low cost and high volume.

**The loop we must demonstrate end to end:**

```
APP BRIEF
  ↓  MARKET RESEARCH (D8): demand keywords → top Shorts → outlier scoring → AI pattern analysis → Market Playbook
  ↓  AI understands the app (brief + optional Play Store reviews + Market Playbook)
  ↓  Generate UGC concepts, hooks, scripts, captions, CTAs (grounded in proven market patterns)
  ↓  Create many variations (concept × hook × language × creator style)
  ↓  Render short-form vertical videos
  ↓  Publish / test (real or simulated)
  ↓  Collect performance data
  ↓  AI identifies winning patterns
  ↓  Generate the next, improved batch
  ↓  TEST AGAIN (Generation 2, 3, ...)
  ↓  Refresh market research weekly; compare market patterns vs our own winners
```

**Success statement for the demo:**
> "We built a working AI growth engine that turns one app brief into a continuous pipeline of India-specific UGC experiments, measures their performance, and uses those learnings to generate the next batch — at near-zero cost per creative."

## 2. Deliverables → where they live in the code

| # | Deliverable | Modules | Dashboard page |
|---|---|---|---|
| D1 | AI UGC Generator (hooks, scripts, captions, videos) | `concepts.py`, `scripts.py`, `tts.py`, `visuals.py`, `captions.py`, `render.py` | Generate, Studio |
| D2 | Indian Localization (Hinglish/Hindi/regional, local scenarios) | `config/languages.yaml`, `config/india_context.yaml`, `localize.py` | Generate |
| D3 | Content Variations from one brief | `variations.py` | Generate, Studio |
| D4 | Performance Dashboard (views, engagement, CTR, installs, CPI, etc.) | `metrics.py`, `simulator.py`, `tracking.py` | Performance |
| D5 | AI Insights (which formats/hooks/messages win) | `insights_perf.py` | Insights |
| D6 | Growth Loop (auto-generate next batch from data) | `optimizer.py`, `loop.py` | Growth Loop |
| D7 | Working Prototype / Demo (brief → generate → test → insights → next gen) | `app.py`, `cli.py`, `demo.py` | Home ("Run Full Loop") + Economics |
| D8 | Market Research (what's working now) | `engine/research/*.py` | Market Research |

The dashboard's Home page must show a checklist of D1–D8 with a green tick when each has produced output for the current app. Judges should be able to see every deliverable.

## 3. Hard constraints

- **Near-zero running cost.** Free APIs, free tiers, open-source libraries and local compute everywhere, **except**: the Gemini API may be used on paid billing, and Apify (paid per result) is used for Instagram research — both approved by the owner on 2026-09-25. Every paid call logs its actual ₹ cost in the ledger, with a daily Gemini cap (`GEMINI_DAILY_BUDGET_INR`) and a monthly Apify cap (`APIFY_MONTHLY_BUDGET_USD`). The Economics page shows actual spend.
- Runs on a normal laptop CPU. GPU features are optional stretch goals.
- Languages at launch: **Hinglish, Hindi, Tamil, Telugu, Marathi, Bengali**; more can be added via config.
- **Localize, don't translate.** Each language version is generated natively with local scenarios, not machine-translated from English.
- Every video carried a small visible **"AI-generated"** label in the original spec. The owner turned the on-video tag off on 2026-09-29 (`SHOW_AI_LABEL=1` restores it); `caption.txt` still carries the `#AIGenerated` disclosure line, and the platform's own AI-content disclosure setting should be used when publishing.
- **No fake testimonials** ("I'm Priya from Pune and this app changed my life"), no invented statistics, no guaranteed-outcome claims. Content is creator-style, narrator, POV, or explainer.
- Simulated performance data must always be clearly labelled "SIMULATED" in the UI and database.
- Secrets in `.env`; provide `.env.example`.
- Cache every external call (LLM, TTS, stock search) on disk.

## 4. Tech stack (all free)

| Job | Primary (free) | Fallback (free) |
|---|---|---|
| Language | Python 3.11+ | — |
| Dashboard | Streamlit + Plotly | — |
| Database | SQLite (`sqlmodel`) | — |
| LLM | Google Gemini API (`google-genai`, paid billing allowed, model auto-picked: newest stable Flash) | Groq free tier (`groq`), then local Ollama |
| Market research — YouTube | YouTube Data API v3 (free quota) + YouTube autocomplete | — |
| Market research — Instagram | Apify `apify/instagram-hashtag-scraper` + `apify/instagram-reel-scraper` (paid per result, capped) | skipped (YouTube only) |
| Play Store data (optional enrichment) | `google-play-scraper` | Brief only |
| Text-to-speech | `edge-tts` (free Microsoft neural Indian voices) | `gTTS` |
| Caption timing | `edge-tts` SubMaker | `faster-whisper` (`base`, CPU) |
| Stock visuals | Pexels API (free key) | Pixabay API (free key), then gradient backgrounds |
| Video assembly | Remotion (React + headless Chromium), driven from Python via `remotion/render.mjs` | — (ffmpeg only for audio probing and placeholder images) |
| Captions | Word-timed React captions inside the Remotion composition (Chromium shapes Indic scripts) | — |
| Fonts | Google Noto (Sans, Devanagari, Tamil, Telugu, Bengali) in `assets/fonts/` | — |
| Music | Local royalty-free tracks in `assets/music/` | No music |
| Upload (stretch) | YouTube Data API v3 free quota | Manual upload |
| Stats | `numpy`, `scipy`, `pandas` | — |

Free-tier limits change often: implement retries with exponential backoff, a request throttle, and a provider switch in `.env` (`LLM_PROVIDER=gemini|groq|ollama`).

## 5. Project structure

```
ugc-growth-engine/
├── PLAN.md  README.md  .env.example  requirements.txt
├── config/
│   ├── languages.yaml        # language → voices, font, script, style notes
│   ├── india_context.yaml    # festivals, cricket/IPL, exams, salary day, city tiers, local scenarios
│   ├── formats.yaml          # UGC formats + structure
│   ├── creator_styles.yaml   # faceless creator styles (see §7.4)
│   ├── research.yaml         # market research settings (see §13 D8.4)
│   └── taxonomy.yaml         # tag values used for insights
├── assets/ fonts/ music/ brand/
├── engine/
│   ├── llm.py                # provider abstraction, JSON validation, cache, retries
│   ├── prompts/              # all prompts as editable .md files (incl. research_*.md)
│   ├── research/             # D8 market research (see §13)
│   │   ├── keywords.py       # demand keywords: LLM seeds + YouTube autocomplete (+ optional pytrends)
│   │   ├── discover.py       # YouTube Data API search + stats, relevance filter, quota tracking
│   │   ├── instagram.py      # Instagram Reels via Apify (hashtags → Reels → creator baselines), capped + cached
│   │   ├── scoring.py        # creator baselines, outlier score, market score, selection
│   │   ├── analyze.py        # Gemini watches each video → taxonomy-aligned JSON
│   │   ├── patterns.py       # pattern statistics (share, weighted share, avg outlier, lift)
│   │   └── playbook.py       # Market Playbook narrated from computed numbers only
│   ├── brief.py              # app brief model + optional Play Store enrichment
│   ├── understand.py         # brief (+ reviews) → personas, pains, phrases, messages
│   ├── concepts.py           # → UGC concepts
│   ├── scripts.py            # concept × hook × language × style → script JSON
│   ├── localize.py           # India-native adaptation rules per language
│   ├── variations.py         # variation matrix, dedupe, predicted scoring, render selection
│   ├── compliance.py         # safety + disclosure checks
│   ├── tts.py  visuals.py  captions.py  render.py
│   ├── tracking.py           # per-creative slug + UTM links
│   ├── metrics.py            # manual entry, CSV import, derived metrics
│   ├── simulator.py          # simulated test market (hidden true effects)
│   ├── insights_perf.py      # statistical winners + LLM narrative
│   ├── optimizer.py          # Thompson sampling, explore/exploit allocation
│   ├── loop.py               # orchestrates one full generation
│   ├── cost.py               # cost + time ledger
│   └── db.py
├── app.py  cli.py  demo.py
├── data/ cache/  engine.db  outputs/<app_id>/gen_<n>/<creative_id>/
├── reference/youtube_content_engine.ipynb   # reference implementation of the D8 core logic
└── tests/
```

## 6. Data model (SQLite)

- **App**: id, name, category, brief (JSON), play_store_id (nullable), icon_path, screenshots (JSON)
- **Understanding**: id, app_id, personas, pain_points, loved_features, user_phrases, key_messages (all JSON)
- **Concept**: id, app_id, generation, title, persona, pain_point, message, india_scenario, format, market_evidence (JSON list of MarketVideo / MarketPattern ids)
- **Creative** (one variation): id, app_id, concept_id, generation, parent_creative_id (nullable), language, hook, hook_type, creator_style, cta_type, length_bucket, script (JSON), post_caption, hashtags, predicted_score, compliance_status, status (draft/approved/rendered/published), tracking_slug, origin (explore/exploit), market_evidence (JSON list of MarketVideo / MarketPattern ids)
- **Video**: id, creative_id, path, thumbnail_path, duration_s, render_seconds
- **Metric**: id, creative_id, source (real/simulated), platform, date, impressions, views, views_3s, avg_watch_pct, likes, comments, shares, saves, clicks, installs, spend_inr, d1_retained, d7_retained
- **Insight**: id, app_id, generation, findings (JSON), narrative, created_at
- **Ledger**: id, step, provider, seconds, cost_inr, paid_equivalent_inr
- **ResearchRun** (D8): id, app_id, started_at, finished_at, quota_used, status
- **Keyword** (D8): id, app_id, run_id, text, language, source (seed/autocomplete), demand_score, trends_interest (nullable)
- **MarketVideo** (D8): id, run_id, platform, video_id, url, title, channel_id, channel_title, published_at, duration_s, views, likes, comments, baseline, outlier_score, market_score, keywords (JSON), relevant, fetched_at
- **MarketAnalysis** (D8): id, market_video_id, analysis (JSON), model, created_at
- **MarketPattern** (D8): id, run_id, dimension, value, videos, share, weighted_share, avg_outlier, lift, signal (pattern/weak)
- **MarketPlaybook** (D8): id, app_id, run_id, playbook (JSON), narrative, created_at

Every Creative must be fully tagged (language, hook_type, format, creator_style, persona, message, cta_type, length_bucket). Tags are what make insights and the loop possible.

## 7. Pipeline specs

### 7.1 App brief (`brief.py`)
Input form (UI + JSON file for CLI):
```json
{
  "app_name": "", "category": "", "one_liner": "",
  "description": "", "key_features": [""], "usp": "",
  "target_audience": "", "city_tiers": ["1","2","3"],
  "languages": ["hinglish","hi","ta"], "brand_tone": "fun|trustworthy|premium|desi-casual",
  "cta": "Download free on Play Store",
  "play_store_url": "(optional)", "claims_to_avoid": [""]
}
```
If `play_store_url` is given, enrich with `google-play-scraper`: description, screenshots, and ~200 reviews (`country="in"`, `lang` en and hi). Screenshots are downloaded for use in videos. Ship 2 sample briefs in `data/sample_briefs/` (e.g. a fintech/UPI app and an edtech app) for the demo.

### 7.2 Understanding (`understand.py`)
LLM → strict JSON: personas (name, age range, city tier, goal, frustration), pain points with evidence phrases, loved features, real user phrases, 5–8 key messages. Merge relevant moments from `india_context.yaml` for the current and next month.

### 7.3 Concepts (`concepts.py`)
Generate ~10 UGC concepts per app. Each: title, persona, pain point, message, **India-specific scenario** (e.g. "month-end UPI balance check before chai", "PG room exam night", "Diwali shopping budget", "Tier-2 job interview in English"), suggested format.

### 7.4 Formats, hooks, creator styles
`formats.yaml`: pov, problem_solution, three_reasons, before_after, nobody_tells_you, old_way_vs_new_way, myth_vs_fact, day_in_life, reaction_to_problem.

`taxonomy.yaml` hook types: question, bold_claim, relatable_pain, curiosity_gap, number_list, desi_humor, trend_moment.

`creator_styles.yaml` (all free, faceless — no avatar needed):
| style | how it renders |
|---|---|
| `bestie_narrator` | casual young voice, fast cuts, big captions |
| `explainer_didi_bhaiya` | calm, trustworthy voice, screenshots + zooms |
| `meme_text` | text-on-screen meme style, punchy captions, minimal voice |
| `screen_demo` | app screenshots front and centre, step-by-step |
| `storytime` | slower narrative, relatable scenario b-roll |

Each style defines: voice choice/gender, TTS rate, caption style (size/colour/position), cut pace, music energy, proportion of screenshot vs stock scenes. Talking AI avatars are a stretch goal only.

### 7.5 Scripts + localization (`scripts.py`, `localize.py`)
One script per creative, strict JSON:
```json
{
  "hook": "", "hook_type": "", "format": "", "language": "", "creator_style": "",
  "scenes": [
    {"voiceover": "", "on_screen_text": "max 6 words",
     "visual": {"type": "stock|screenshot", "query_en": "", "screenshot_index": 0},
     "est_seconds": 4}
  ],
  "cta": "", "cta_type": "download|try_free|link_in_bio|share",
  "post_caption": "", "hashtags": [""],
  "predicted_score": 0, "score_reason": ""
}
```
Rules for prompts:
- 20–35 seconds, 5–8 scenes. The hook is line 1 and must work on mute (on-screen text).
- **Localize natively per language** using `languages.yaml` style notes: Hinglish in Roman script with natural Gen-Z texting tone; Hindi/Marathi in Devanagari; Tamil, Telugu, Bengali in native script; English tech/brand words may stay English. Use local scenarios, food, places, and references appropriate to that language's audience (e.g. Chennai/Coimbatore for Tamil, Pune/Nagpur for Marathi) — do not just translate.
- Use real user phrases where natural. No fake testimonials, invented numbers, or claims outside the brief.
- `query_en` always English (for stock search).

### 7.6 Variation engine (`variations.py`) — D3
- Build the matrix: **concepts × hooks per concept × languages × creator styles**. Example: 10 × 5 × 3 × 2 = **300 creatives**.
- Generating scripts is cheap; rendering is slow. So:
  1. Generate hooks for all combinations in batched LLM calls (many hooks per call).
  2. Generate full scripts for a configurable subset (default 60).
  3. Dedupe near-identical hooks (simple token similarity).
  4. Score each with `predicted_score` (LLM rating + heuristic: hook length, has number/question, pain-word match).
  5. Render the top N (default 12 for Gen 1), ensuring coverage across languages, hook types, and styles (stratified selection, not just top scores).
- UI shows the full matrix count vs. scripted count vs. rendered count.

### 7.7 Compliance (`compliance.py`)
Rule-based check then LLM check → `{pass, issues[]}`. Flag fake testimonial patterns, invented stats, guaranteed outcomes ("earn ₹X daily", "100% guaranteed"), health/finance promises, religious/political targeting, competitor defamation, anything in `claims_to_avoid`. Flagged items need manual approval.

### 7.8 Voice (`tts.py`)
`edge-tts` per scene; voices from `languages.yaml`, e.g. hi/hinglish `hi-IN-SwaraNeural`, `hi-IN-MadhurNeural`; ta `ta-IN-PallaviNeural`, `ta-IN-ValluvarNeural`; te `te-IN-ShrutiNeural`, `te-IN-MohanNeural`; mr `mr-IN-AarohiNeural`, `mr-IN-ManoharNeural`; bn `bn-IN-TanishaaNeural`, `bn-IN-BashkarNeural`. Verify names at startup via `edge-tts --list-voices` and give a clear error if missing. Rate per creator style. Cache by hash(text, voice, rate). Fallback gTTS + faster-whisper timings.

### 7.9 Visuals (`visuals.py`)
Stock: Pexels video search (portrait preferred) → Pexels photo → Pixabay → gradient. Screenshot scenes: app screenshot over a blurred copy of itself filling 9:16, slow zoom. Download smallest file ≥720p. Cache by query. Save attribution in metadata.

### 7.10 Captions (`captions.py`) — critical for Indian scripts
`captions.py` builds word-timed caption lines (2–4 words per line, current-word highlight) and a per-language font/style; the Remotion composition draws them. Text is adaptive: `textfit.py` samples each scene's footage and chooses the calmest band, light/dark text, an accent colour and an optional plate; each creator style sets its own display font, animation and cut transition. Chromium shapes Indic scripts correctly and falls back per glyph to Noto Sans for Latin words. **Never** render Indic text with plain Pillow unless `PIL.features.check('raqm')` is true; use Remotion stills for thumbnails.

### 7.11 Render (`render.py`)
1080×1920, 30 fps, H.264 crf 23, AAC 128k, rendered by Remotion (`remotion/src/UgcVideo.tsx`; Python writes a props JSON of scene clips, voice tracks, caption timings, hook and end card, then `remotion/render.mjs` renders it). Per creative: scene clips cropped to 9:16 and fitted to audio length → cuts per style → hook text overlay for first 2s → voice + music at about −20 dB with fade, gains tuned so the mix lands near −14 LUFS (no loudnorm pass) → word-highlight captions → 2s end card (icon, app name, CTA) → corner "AI-generated" label → thumbnail still from hook frame → save `script.json`, `caption.txt` (caption + hashtags + tracking link). Log render time. Target: under 60s per 30s video on CPU. Render in a worker pool (2 processes) and never let one failure stop the batch.

### 7.12 Tracking (`tracking.py`)
Each creative gets a slug. Link format:
`https://play.google.com/store/apps/details?id=<pkg>&referrer=utm_source%3D<platform>%26utm_medium%3Dai_ugc%26utm_campaign%3D<app>_gen<n>%26utm_content%3D<slug>`
Optional FastAPI `redirect.py` (`/r/<slug>` logs click then redirects) — stretch.

### 7.13 Performance data (`metrics.py`, `simulator.py`) — D4
Three sources, same table, `source` column distinguishes them:
1. **Manual entry** form per creative.
2. **CSV import** (columns match the Metric table; `slug` identifies the creative).
3. **Simulated test market** (`simulator.py`) so the full loop can be demonstrated in minutes:
   - Hidden "true" effect sizes per tag (language, hook_type, format, creator_style, message, length_bucket), randomly seeded per app and stored hidden.
   - For each creative: impressions ~ fixed budget; views, 3s views, watch %, engagement, clicks, installs drawn from Binomial/Beta distributions using the combined true effects + noise.
   - The optimizer never reads the hidden effects. A debug toggle can reveal them after the demo to prove the loop learned the truth.
   - Always labelled "SIMULATED" in charts.

Derived metrics (computed, never stored manually):
- Hook rate = views_3s / impressions
- Avg watch % (hold)
- Engagement rate = (likes + comments + shares + saves) / views
- CTR = clicks / views
- Install rate (CVR) = installs / clicks
- Installs per 1k views
- CPI = spend / installs (organic spend = 0 → show "₹0 (organic)")
- D1 / D7 retention when provided

**Primary optimization metric:** installs per 1k views. Fallbacks when installs are missing: CTR → engagement rate × avg watch %. Configurable.

### 7.14 AI Insights (`insights_perf.py`) — D5
1. **Statistics first:** for each tag dimension, compute each value's primary metric, **lift vs. overall average**, sample size, and a Bayesian probability of being best (Beta posteriors, 5,000 draws). Require a minimum sample (default ≥ 3 creatives and ≥ 500 views) before calling anything a winner; otherwise mark "needs more data."
2. Detect top **combinations** (e.g. language × hook_type) with the same thresholds.
3. **Then the LLM narrates** from the computed numbers only (never invents numbers): 3–5 findings like "Tamil + relatable_pain hooks get 2.1× installs per 1k views (82% probability best)," plus 2–3 recommendations and 1–2 "stop doing" items.
4. Show the example hook text of the top 3 and bottom 3 creatives so humans can see *why*.

### 7.15 Growth Loop (`optimizer.py`, `loop.py`) — D6
One click "Generate Next Batch" does:
1. Thompson sampling over each tag dimension using the latest metrics.
2. Allocate the next batch: **70% exploit** (variations of winners + winning tag combos) and **30% explore** (under-tested combos, new concepts). Ratio configurable.
3. Exploit creatives: take top 20% of creatives, write 3–5 new hook variants each and some new concepts built on the winning message; set `parent_creative_id` and `generation = n + 1`.
4. The LLM prompt for Gen n+1 receives the insight findings as explicit guidance (winning hooks, languages, messages, and what to avoid).
5. Compliance → render → (simulate or wait for real data) → insights → repeat.
6. `loop.py --generations 3` runs multiple generations automatically in simulated mode.

Dashboard must show **generation-over-generation improvement**: avg installs per 1k views and CTR by generation, plus the share of batch allocated to each winning arm.

### 7.16 Cost & time (`cost.py`)
Log every step with seconds and ₹. Show: cost per creative (₹0), time per creative, creatives per hour, experiments possible per week, and comparison with a traditional workflow (brief → creator → negotiation → shoot → revisions → approval). The creator cost and turnaround days come from `.env` (`CREATOR_REEL_COST_INR`, `CREATOR_TURNAROUND_DAYS`) — not hard-coded.

## 8. Dashboard (`app.py`, Streamlit)

1. **Home** — deliverables checklist D1–D8, app selector, big **"Run Full Loop (Demo)"** button: brief → market research (cached run if < 7 days old) → understand → concepts → variations → render 6 → simulate → insights → Gen 2 → render 6 → simulate → show improvement.
2. **Brief** — form or load sample brief; optional Play Store enrichment.
2b. **Market Research** — demand keywords table with scores, top videos grid (thumbnail via YouTube thumbnail URL, views, outlier ×, tags, link), pattern charts (weighted share + avg outlier per dimension), winning combinations, the Market Playbook, "Refresh research" button, quota used.
3. **Understand** — personas, pain points, user phrases, key messages, India moments.
4. **Generate** — pick languages, formats, styles, hooks per concept; show the variation matrix counts; table of creatives with tags, predicted score, compliance status; approve/edit/reject.
5. **Studio** — render queue with progress, video grid (players, thumbnails, tags), download, copy caption + tracking link.
6. **Performance** — data source toggle (real/simulated/all), KPI cards (views, hook rate, watch %, engagement, CTR, installs, CPI), charts by language, hook type, format, style, message; creative leaderboard; manual entry + CSV import.
7. **Insights** — statistical tables with lift + probability best, AI narrative, top/bottom creatives side by side.
8. **Growth Loop** — explore/exploit allocation, "Generate Next Batch" button, generation-over-generation charts, lineage view (parent → child creatives).
9. **Economics** — cost/time per creative, throughput, traditional vs. engine comparison.

## 9. Config examples

`config/languages.yaml`
```yaml
hinglish: {name: Hinglish, script: latin, voices: [hi-IN-SwaraNeural, hi-IN-MadhurNeural], font: "Noto Sans",
           style_notes: "Roman script, Gen-Z texting tone, metro + tier-2 references"}
hi: {name: Hindi, script: devanagari, voices: [hi-IN-SwaraNeural, hi-IN-MadhurNeural], font: "Noto Sans Devanagari",
     style_notes: "simple spoken Hindi, Hindi-belt tier-2/3 scenarios"}
ta: {name: Tamil, script: tamil, voices: [ta-IN-PallaviNeural, ta-IN-ValluvarNeural], font: "Noto Sans Tamil",
     style_notes: "spoken Tamil, Tamil Nadu references, avoid Hindi-belt idioms"}
te: {name: Telugu, script: telugu, voices: [te-IN-ShrutiNeural, te-IN-MohanNeural], font: "Noto Sans Telugu",
     style_notes: "spoken Telugu, AP/Telangana references"}
mr: {name: Marathi, script: devanagari, voices: [mr-IN-AarohiNeural, mr-IN-ManoharNeural], font: "Noto Sans Devanagari",
     style_notes: "spoken Marathi, Maharashtra references"}
bn: {name: Bengali, script: bengali, voices: [bn-IN-TanishaaNeural, bn-IN-BashkarNeural], font: "Noto Sans Bengali",
     style_notes: "spoken Bengali, West Bengal references"}
```

`config/india_context.yaml`: month → moments (Diwali, Navratri, Durga Puja, Holi, Eid, Pongal, Onam, Ganesh Chaturthi, IPL, World Cup, board exams, JEE/NEET, placement season, salary day, monsoon, wedding season, Republic Day/Independence Day sales), plus reusable everyday scenarios (chai break, local train, PG/hostel life, family WhatsApp group, month-end budget, UPI payments, tier-2 city aspirations).

`.env.example`
```
LLM_PROVIDER=gemini
GEMINI_API_KEY=
GROQ_API_KEY=
OLLAMA_MODEL=llama3.1
PEXELS_API_KEY=
PIXABAY_API_KEY=
PRIMARY_METRIC=installs_per_1k_views
EXPLORE_RATIO=0.3
GEN1_RENDER_COUNT=12
CREATOR_REEL_COST_INR=10000
CREATOR_TURNAROUND_DAYS=7
SIM_SEED=42
```

## 10. Build phases and acceptance checks

**Phase 0 — Setup.** Structure, requirements, README (ffmpeg install, `scripts/get_fonts.py` to download Noto fonts), `.env.example`, sample briefs.
✅ `python -c "import engine"`; `node -v` and `ffmpeg -version`; `cd remotion && npm install`; fonts present; edge-tts voices listed.

**Phase 1 — Brief + understanding (D1 start, D2 start).** `llm.py` (provider switch, JSON validation + retry, cache), `brief.py`, `understand.py`, `db.py`.
✅ `python cli.py understand --brief data/sample_briefs/fintech.json` saves valid personas, pains, phrases, messages.

**Phase 1.5 — Market Research (D8).** Build order: `keywords.py` → `discover.py` → `scoring.py` → `analyze.py` → `patterns.py` → `playbook.py` → DB tables → `cli.py research` → integration (§13 D8.6) → dashboard page.
✅ `python cli.py research --brief data/sample_briefs/fintech.json` completes with ≥ 15 demand keywords, ≥ 100 candidate Shorts, ≥ 20 analysed videos, and a stored MarketPlaybook; quota used logged and under budget.
✅ All analysis tags validate against `taxonomy.yaml` / `formats.yaml` / `creator_styles.yaml` (invalid → retried once, then "other").
✅ Patterns table shows share, weighted share, avg outlier and lift; values with < 3 videos marked "weak signal".
✅ Playbook narrative contains only numbers present in the patterns table (test: every number in the narrative appears in the data).
✅ Re-running within the cache window costs 0 Gemini calls for already-analysed videos.
✅ Gen 1 concepts: ≥ 60% have non-empty `market_evidence`.
✅ Optimizer priors reflect market patterns before Gen 1 metrics exist, and are overridden after Gen 2 in simulation.
✅ A planted script line copying a stored hook is flagged by compliance.
✅ Tests: autocomplete parser, outlier/baseline maths, relevance filter (Latin + Devanagari), tag validation, prior initialisation, stale-data purge.

**Phase 2 — Concepts, scripts, localization, compliance (D1, D2).**
✅ 10 concepts; scripts in Hinglish, Hindi, Tamil that read natively (not translated); a planted fake testimonial is flagged.

**Phase 3 — Variation engine (D3).**
✅ Matrix of ≥ 300 potential creatives shown; ≥ 60 scripted; stratified top 12 selected covering all chosen languages and styles; duplicates removed.

**Phase 4 — Voice, captions, render (D1).**
✅ 12 videos rendered at 1080×1920, 20–40s, with voice, captions (Hindi/Tamil conjuncts correct — extract a frame to verify), hook overlay, music, end card, AI label, thumbnail; render time and ₹0 logged.

**Phase 5 — Tracking, metrics, simulator (D4).**
✅ Every creative has a tracking link; CSV import works; simulator fills metrics labelled SIMULATED; Performance page shows all KPIs and breakdowns.

**Phase 6 — AI insights (D5).**
✅ Statistical tables with lift and probability best; LLM narrative cites only computed numbers; "needs more data" appears when samples are small.

**Phase 7 — Growth loop (D6).**
✅ `python cli.py loop --generations 3 --simulated` runs end to end; Gen 2 and Gen 3 show higher average primary metric than Gen 1 in simulation; revealed hidden effects match the tags the optimizer favoured.

**Phase 8 — Dashboard + demo (D7).**
✅ From a fresh DB, "Run Full Loop (Demo)" completes brief → Gen 2 videos in under ~10 minutes; all D1–D8 ticks turn green; Economics page populated. The demo runs market research first (uses the cached run if < 7 days old).

**Stretch (only after Phase 8):** YouTube Shorts auto-upload + view sync; FastAPI click-redirect; WhatsApp share-card generator (1080×1080); open-source talking avatar on free Colab GPU; deploy to Streamlit Community Cloud or Hugging Face Spaces.

## 11. Code quality rules

- Type hints, small functions, `logging`/`rich` logs.
- Every external call: timeout, retry with backoff, cache, friendly UI error.
- One failed creative never stops a batch.
- Prompts live in `engine/prompts/*.md`.
- Tests for: JSON parsing, compliance rules, caption building, derived metrics, insight thresholds, optimizer allocation, simulator determinism with a fixed seed.

## 12. What the demo must prove (maps to the 6 questions)

| Question | Proof in the demo |
|---|---|
| Do we know what works before spending? | Market Research page: demand keywords, outlier videos, proven formats, and Gen 1 concepts linked to market evidence |
| Can AI create UGC? | Play rendered short-form videos from the Studio page |
| Can it create content for India? | Same concept shown in Hinglish, Hindi, and Tamil with local scenarios |
| Can it create content at scale? | Variation matrix: 300 possible → 60 scripted → 12 rendered, ₹0 each, time per creative |
| Can we identify what works? | Performance dashboard + Insights page with lift and probability best |
| Can it improve continuously? | Growth Loop: Gen 1 → Gen 2 → Gen 3 improvement chart and creative lineage |

**Demo script (5 minutes):** load sample brief → show understanding (real user phrases) → generate variations (show matrix) → play 2 videos in different languages → show a compliance flag → simulate test → Insights → "Generate Next Batch" → Gen 2 videos + improvement chart → Economics page → closing line from §1. If we posted real videos to a test account, show those real numbers alongside the simulation.

## 13. Market Research (D8) — spec

Reference implementation of the core logic: `reference/youtube_content_engine.ipynb` (reuse its search, baseline, outlier-score, Gemini-analysis and pattern-count logic, restructured into `engine/research/`). All other rules still apply (₹0 cost, cache every call, retries, one failure never stops a batch).

### D8.1 Why

Today the engine generates concepts from the app brief alone. Its Gen 1 is a guess. D8 makes Gen 1 an
**informed guess**: before generating anything, the engine studies what people in India actually search
for about the app's problem, finds the short videos that are over-performing right now, and extracts the
formats, hooks and angles behind them. Generation then starts from proven patterns, and the growth loop
refines from there with our own data.

Updated loop:

```
APP BRIEF
  ↓  MARKET RESEARCH (D8): demand keywords → top Shorts → outlier scoring → AI pattern analysis → Market Playbook
  ↓  AI understands the app (brief + reviews + Market Playbook)
  ↓  Generate concepts / hooks / scripts grounded in proven patterns
  ↓  ... (rest of loop unchanged) ...
  ↓  Refresh market research weekly; compare market patterns vs our own winners
```

### D8.2 Pipeline

### Step 1 — Demand keywords (`engine/research/keywords.py`)
Goal: find the queries people actually search, not the product name.
1. **Seed:** LLM expands the brief into 30–50 *problem-style* queries per target language
   (e.g. for a motor insurance calculator: "car insurance renewal", "IDV kya hota hai",
   "bike insurance kitne ka hota hai", "claim rejected", "third party vs comprehensive").
2. **Autocomplete expansion:** for each seed, fetch YouTube search suggestions
   (`https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=<lang>&gl=IN&q=<seed>`).
   Unofficial endpoint: cache, throttle (≥1 s between calls), and skip silently if it fails.
   Suggestions appearing for multiple seeds are strong demand signals.
3. **Optional trend signal:** `pytrends` interest over time (geo `IN`, last 90 days). Unofficial and
   rate-limited: treat as optional enrichment, never a hard dependency.
4. **Demand score per keyword** (0–1): combine autocomplete frequency, pytrends interest (if available),
   and total views of the top 10 results from Step 2. Keep the top K keywords (`RESEARCH_TOP_KEYWORDS`, default 20).

### Step 2 — Discover videos (`engine/research/discover.py`)
YouTube Data API v3 (free 10,000 units/day):
- `search.list` per keyword: `type=video`, `videoDuration=short`, `regionCode=IN`, `order=viewCount`,
  `publishedAfter=now − RESEARCH_DAYS_BACK`. 100 units per call.
- `videos.list` in batches of 50 for stats and duration (1 unit per batch). Keep ≤ 180 s and ≥ `RESEARCH_MIN_VIEWS`.
- Relevance filter: brief-derived term lists (category terms × audience/object terms), word-boundary
  matching for Latin script, substring for Indic scripts.
- Track quota in the ledger; stop cleanly before `YOUTUBE_DAILY_QUOTA_BUDGET` is exceeded.

### Step 3 — Outlier scoring (`engine/research/scoring.py`)
- Creator baseline = median views of the channel's last 30 uploads (Shorts only if ≥ 5, else all);
  fallback = channel total views ÷ video count.
- `outlier_score = views / max(baseline, 200)`.
- `market_score = 0.55·pct(outlier) + 0.25·pct(views_per_day) + 0.20·pct(engagement_rate)`.
- Select top `RESEARCH_ANALYZE_N` (default 25) with max 2 per channel.

### Step 4 — AI video analysis (`engine/research/analyze.py`)
Gemini watches each selected video by URL (`types.Part(file_data=types.FileData(file_uri=url))`) and
returns strict JSON. **Tag values must come from `config/taxonomy.yaml` and `config/formats.yaml`** so market
patterns map 1:1 to our generation tags:

```json
{
  "is_relevant": true,
  "topic_angle": "under 12 words",
  "persona_guess": "who the video speaks to",
  "hook_first_3s": "what is said and shown",
  "hook_type": "<taxonomy.hook_types>",
  "format": "<formats.yaml key>",
  "language": "<languages.yaml key or 'english'>",
  "production_style": "talking_head | in_car | skit | animation | green_screen | screen_demo | text_broll | other",
  "closest_creator_style": "<creator_styles.yaml key>",
  "emotional_trigger": "fear_of_loss | save_money | confusion_to_clarity | anger | curiosity | social_proof | other",
  "message": "the core message in one line",
  "structure": ["3-6 beats"],
  "uses_numbers": true,
  "shows_app_or_screen": true,
  "cta_type": "<taxonomy cta types or 'follow' | 'comment_keyword' | 'none'>",
  "length_bucket": "<taxonomy length buckets>",
  "why_it_worked": "1-2 sentences"
}
```
Model auto-selected (newest stable Flash on the key), temperature 0.2, cache by video_id, ≥ 7 s between calls,
backoff on 429. Videos Gemini cannot open are logged and skipped.

### Step 5 — Patterns (`engine/research/patterns.py`)
Statistics first, same rules as §7.14:
- For each tag dimension: count, share %, **performance-weighted share %** (weighted by `market_score`),
  average outlier score, lift vs. overall average.
- Top combinations (format × hook_type × language, and topic_angle clusters).
- Minimum sample: a value needs ≥ 3 videos to be called a pattern; otherwise "weak signal".

### Step 6 — Market Playbook (`engine/research/playbook.py`)
LLM narrates **only from computed numbers** (never invents numbers) into a stored `MarketPlaybook`:
- Top 5 demand keywords/questions (with demand score)
- 3 proven format recipes: format + hook type + language + creator style, with evidence (numbers + 2 example URLs)
- 10 hook *templates* (abstracted, fill-in-the-blank — never copied lines)
- Top topic angles and messages to build concepts on
- "Avoid" list (patterns common in the sample but under-performing)
- Gaps: high-demand keywords with few good videos (content opportunities)

### D8.3 Data model additions (§6)

- **Keyword**: id, app_id, run_id, text, language, source (seed/autocomplete), demand_score, trends_interest (nullable)
- **MarketVideo**: id, run_id, platform, video_id, url, title, channel_id, channel_title, published_at, duration_s,
  views, likes, comments, baseline, outlier_score, market_score, keywords (JSON), relevant, fetched_at
- **MarketAnalysis**: id, market_video_id, analysis (JSON), model, created_at
- **MarketPattern**: id, run_id, dimension, value, videos, share, weighted_share, avg_outlier, lift, signal (pattern/weak)
- **MarketPlaybook**: id, app_id, run_id, playbook (JSON), narrative, created_at
- **ResearchRun**: id, app_id, started_at, finished_at, quota_used, status

Add `market_evidence` (JSON list of MarketVideo ids / pattern ids) to **Concept** and **Creative**.

### D8.4 Config (`config/research.yaml` + `.env`)

```yaml
days_back: 180
min_views: 3000
max_duration_s: 180
results_per_keyword: 25
top_keywords: 20
analyze_n: 25
max_per_channel: 2
baseline_sample: 30
market_prior_strength: 5        # pseudo-observations given to market patterns in the optimizer
proven_concept_share: 0.6       # share of Gen 1 concepts that must build on a proven pattern
refresh_days: 7
```

`.env.example` additions:
```
YOUTUBE_API_KEY=
YOUTUBE_DAILY_QUOTA_BUDGET=8000
```

### D8.5 Compliance & data rules

- Use only the official YouTube Data API for stats. Do not download or re-host competitor videos.
- Store metadata + our own analysis only. Delete or refresh stored YouTube API data older than 30 days
  (YouTube API Services policy); `cli.py research --purge-stale` does this.
- Hook templates must be abstracted patterns, never verbatim lines from other creators. `compliance.py`
  gets a new rule: flag any script line with high token overlap (> 0.6) with a stored `hook_first_3s`.
- Instagram is out of scope for Phase 1.5. Stretch: CSV import of reel URLs + stats gathered manually or via the
  official Instagram Graph API hashtag search (Business account, 30 hashtags/week).

### D8.6 Integration with existing modules

| Module | Change |
|---|---|
| `understand.py` | Input now includes the latest MarketPlaybook: demand questions become pain points; topic angles and messages merge into key_messages (marked `source: market`). |
| `concepts.py` | ≥ `proven_concept_share` of Gen 1 concepts build on a proven topic angle/format; each stores `market_evidence`. The rest are free exploration. |
| `scripts.py` | Prompt receives the 3 format recipes + hook templates as guidance (not copy). Hinglish/Hindi share from market data informs language emphasis. |
| `variations.py` | `predicted_score` adds a **market prior** term: weighted share / lift of the creative's tags in the market patterns. Stratified selection unchanged. |
| `optimizer.py` | Thompson sampling Beta priors initialised from market patterns, capped at `market_prior_strength` pseudo-observations, so our own data overrides the market within a few generations. |
| `insights_perf.py` | New section: "Market vs. us" — compares market-proven patterns with our own winners (agree / disagree / untested). |
| `loop.py` | Runs research first if no playbook exists or the latest is older than `refresh_days`. |
| `simulator.py` | Unchanged (hidden effects stay independent of market data, so the demo still proves learning). |
