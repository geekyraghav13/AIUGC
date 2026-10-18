# PLAN ADDENDUM — D8 Market Research ("What's working now")

> **Instructions for Claude Code:** This addendum adds a new deliverable, **D8**, to PLAN.md.
> Merge it into PLAN.md (update §1, §2, §5, §6, §8, §10 and §12 as described in "Changes to existing sections"),
> then build **Phase 1.5** before continuing with the remaining phases. If Phases 2+ already exist, integrate
> D8 into the existing modules as described in §D8.6. Reference implementation of the core logic:
> `reference/youtube_content_engine.ipynb`. Reuse its search, baseline, outlier-score, Gemini-analysis and
> pattern-count logic; restructure it into the modules below. All other rules in PLAN.md still apply
> (₹0 cost, cache every call, retries, one failure never stops a batch).

---

## D8.1 Why

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

## D8.2 Pipeline

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

## D8.3 Data model additions (§6)

- **Keyword**: id, app_id, run_id, text, language, source (seed/autocomplete), demand_score, trends_interest (nullable)
- **MarketVideo**: id, run_id, platform, video_id, url, title, channel_id, channel_title, published_at, duration_s,
  views, likes, comments, baseline, outlier_score, market_score, keywords (JSON), relevant, fetched_at
- **MarketAnalysis**: id, market_video_id, analysis (JSON), model, created_at
- **MarketPattern**: id, run_id, dimension, value, videos, share, weighted_share, avg_outlier, lift, signal (pattern/weak)
- **MarketPlaybook**: id, app_id, run_id, playbook (JSON), narrative, created_at
- **ResearchRun**: id, app_id, started_at, finished_at, quota_used, status

Add `market_evidence` (JSON list of MarketVideo ids / pattern ids) to **Concept** and **Creative**.

## D8.4 Config (`config/research.yaml` + `.env`)

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

## D8.5 Compliance & data rules

- Use only the official YouTube Data API for stats. Do not download or re-host competitor videos.
- Store metadata + our own analysis only. Delete or refresh stored YouTube API data older than 30 days
  (YouTube API Services policy); `cli.py research --purge-stale` does this.
- Hook templates must be abstracted patterns, never verbatim lines from other creators. `compliance.py`
  gets a new rule: flag any script line with high token overlap (> 0.6) with a stored `hook_first_3s`.
- Instagram is out of scope for Phase 1.5. Stretch: CSV import of reel URLs + stats gathered manually or via the
  official Instagram Graph API hashtag search (Business account, 30 hashtags/week).

## D8.6 Integration with existing modules

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

## Changes to existing sections

- **§1:** Insert the MARKET RESEARCH step into the loop diagram (see D8.1).
- **§2:** Add row `D8 | Market Research (what's working now) | engine/research/*.py | Market Research`.
  Home checklist becomes D1–D8.
- **§5:** Add `engine/research/` (keywords, discover, scoring, analyze, patterns, playbook), `engine/prompts/research_*.md`,
  `config/research.yaml`, `reference/youtube_content_engine.ipynb`.
- **§8:** Add dashboard page **2b. Market Research** (after Brief): demand keywords table with scores, top videos grid
  (thumbnail via YouTube thumbnail URL, views, outlier ×, tags, link), pattern charts (weighted share + avg outlier per
  dimension), winning combinations, the Market Playbook, "Refresh research" button, quota used.
- **§10:** Add Phase 1.5 (below). Demo "Run Full Loop" runs research first (use cached run if < 7 days old).
- **§12:** Add row: *"Do we know what works before spending?"* → Market Research page: demand keywords, outlier videos,
  proven formats, and Gen 1 concepts linked to market evidence.

## Phase 1.5 — Market Research (D8)

Build order: `keywords.py` → `discover.py` → `scoring.py` → `analyze.py` → `patterns.py` → `playbook.py` → DB tables →
`cli.py research` → integration (D8.6) → dashboard page.

✅ Acceptance checks
- `python cli.py research --brief data/sample_briefs/fintech.json` completes with ≥ 15 demand keywords, ≥ 100 candidate
  Shorts, ≥ 20 analysed videos, and a stored MarketPlaybook; quota used logged and under budget.
- All analysis tags validate against `taxonomy.yaml` / `formats.yaml` / `creator_styles.yaml` (invalid → retried once, then "other").
- Patterns table shows share, weighted share, avg outlier and lift; values with < 3 videos marked "weak signal".
- Playbook narrative contains only numbers present in the patterns table (test: every number in the narrative appears in the data).
- Re-running within the cache window costs 0 Gemini calls for already-analysed videos.
- Gen 1 concepts: ≥ 60% have non-empty `market_evidence`.
- Optimizer priors reflect market patterns before Gen 1 metrics exist, and are overridden after Gen 2 in simulation.
- A planted script line copying a stored hook is flagged by compliance.
- Tests: autocomplete parser, outlier/baseline maths, relevance filter (Latin + Devanagari), tag validation,
  prior initialisation, stale-data purge.