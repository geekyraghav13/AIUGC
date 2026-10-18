# AI UGC & Social Growth Engine (India)

One app brief goes in. What comes out is a repeating growth loop of short vertical videos (Reels / Shorts) made for
Indian audiences in Hinglish, Hindi, Tamil, Telugu, Marathi and Bengali:

**research what already works → understand the app → write hundreds of tagged video ideas → produce the best ones as
1080×1920 videos → test them → learn which ingredients win → make a better next batch.**

Every step is saved, tagged and costed. Most of the work runs on free tools and a laptop CPU. The paid services
(Gemini, Apify, Magnific) are capped, and every paid call is logged in rupees or credits.

---

## 0. Quick start — run the engine

### Start it (every time)
```bash
cd /Users/user/Documents/AIUGC/ugc-growth-engine
.venv/bin/streamlit run app.py --server.port 8501        # then open http://localhost:8501
```
**Restart after any code change.** Hot reload is off (`.streamlit/config.toml`), because reloading engine modules
crashes SQLModel. To restart:
```bash
pkill -f "streamlit run app.py"; .venv/bin/streamlit run app.py --server.port 8501
```

### Use it: the Engine page (one guided flow)
1. **Choose an app**, or **Create new app**. You fill in the brief; a Play Store link pulls in real screenshots,
   the icon and reviews. The trash icon deletes an app, after you type its name to confirm (spend history is kept).
2. **Go →** runs market research (reuses this week's unless *Fresh research* is ticked), then understanding, then
   **10 scripts**. Languages and video types come from that app's research. Nothing is filmed or paid for yet.
3. **Scripts & approval.** Edit lines, reject scripts you don't want, then approve. This fixes a Magnific credit
   ceiling of the estimate + 15%, and production starts one video at a time.
4. **Voice lines.** Magnific's cast Indian voices only work through the Magnific MCP, so ask Claude:
   *"record the voices for batch N"*. Claude runs `python cli.py batch voice-jobs N`, records each line, imports it
   (`crew import`) and restarts `batch run N`.
5. **Production board.** Each video card shows its status, crew progress, player, QC score and credits.
   - **Download** icon on each card; **Download all** (a .zip with caption files) above the board.
   - **Approve**, or **Send back** with a note.
   - Search and filter by status, language or type.

The older pages (Research details, Studio, Performance, Insights, Growth Loop, Economics, and so on) are under
**Advanced** in the sidebar.

### Same flow from the terminal
```bash
.venv/bin/python cli.py go cashcalc              # research → understanding → 10 scripts
.venv/bin/python cli.py batch status <id>         # scripts, mix, credits
.venv/bin/python cli.py batch approve <id>        # approve (sets the credit ceiling)
.venv/bin/python cli.py batch voice-jobs <id>     # voice lines Claude must record via Magnific MCP
.venv/bin/python cli.py crew import <run> <request_id> "<url>" --credits N --creation "<url>"
.venv/bin/python cli.py batch run <id>            # produce one by one (re-run to continue after a stop)
.venv/bin/python cli.py magnific-check            # is the Magnific API key accepted?
.venv/bin/python -m pytest -q                     # 86 tests, offline
```

### Keys and caps (`.env`; see `.env.example`)
- **Keys:** `GEMINI_API_KEY`, `YOUTUBE_API_KEY`, the Apify token, `PEXELS_API_KEY` (optional `PIXABAY_API_KEY`),
  `MAGNIFIC_API_KEY`, `MAGNIFIC_WEBHOOK_SECRET`.
- **Gemini:** `GEMINI_DAILY_BUDGET_INR` (default ₹500). When it's reached, AI review is skipped. Only the machine
  checks run, and each video gets a note saying it wasn't AI-reviewed. Raise the cap or wait for the next day.
- **Magnific:** the monthly cap and per-mode caps are in `config/production.yaml`, and each batch has its own
  approved ceiling. Ask Claude to check the balance before a big run.

### Where the output goes
- **Videos:** `outputs/<app>/gen_<n>/<creative_id>/production/v<N>/video.mp4`, with `thumbnail.jpg`,
  `caption.txt` and `script.json` beside each one.
- **Downloads folder:** Claude copies the Cash Calculator pilot videos to `~/Downloads/ugc_pilot_cashcalc/`.

---

## 1. Tools and services at a glance

| Job | Tool / service | Free or paid | Used in step |
|---|---|---|---|
| Thinking and writing (ideas, hooks, scripts, reviews, insights) | **Google Gemini API** (`google-genai`, newest stable Flash picked automatically) | Paid, ₹ cap per day | ①–⑧ |
| LLM fallbacks | Groq (free tier), then local Ollama | Free | ①–⑧ |
| What Indians search for | **YouTube autocomplete** (`suggestqueries.google.com`) | Free, unofficial | ① |
| Search trend signal (optional) | `pytrends` (Google Trends, India, 90 days) | Free, unofficial | ① |
| Finding Shorts and their stats | **YouTube Data API v3** (search, videos, channels, playlistItems) | Free, 10,000 units/day | ① |
| Finding Instagram Reels and their stats | **Apify** actors `apify/instagram-hashtag-scraper` + `apify/instagram-reel-scraper` | Paid per result, $ cap per month | ① |
| AI watching the top Shorts | Gemini video understanding (by YouTube URL) | Paid (in the Gemini cap) | ① |
| App description, reviews, screenshots | `google-play-scraper` (country `in`, English and Hindi reviews) | Free | Brief |
| Voice (fast path) | **edge-tts**: Microsoft neural Indian voices, with word timings; `gTTS` fallback | Free | ⑤ |
| Recurring characters (per app) | **Magnific** → Seedream portraits + character library; Kling 2.5 clips; OmniHuman lip-sync | Paid, credits | ④b ⑤b |
| Voice (production crew) | **Magnific** → ElevenLabs `eleven_v3`, cast Hindi/Tamil creator voices | Paid, credits | ⑤b |
| Stock footage | **Pexels** (video, then photo) → **Pixabay** → gradient background | Free (API keys) | ⑤ |
| AI images and clips for missing shots | **Magnific** → Seedream 4 images, Kling 2.5 clips | Paid, credits | ⑤b |
| Video assembly, captions, animation, thumbnail | **Remotion** (React + headless Chromium), driven from Python | Free for individuals / teams ≤ 3 | ⑤ ⑤b |
| Text placement and colours per scene | `textfit.py` (Pillow + numpy looking at each scene's frames) | Free | ⑤ ⑤b |
| Caption timing for imported voices | `faster-whisper` (base model, CPU) | Free | ⑤b |
| Audio and file checks | `ffmpeg` / `ffprobe` (loudness EBU R128, silences, frames, cover art) | Free | ⑤b QC |
| Fonts | Google Fonts: Noto (all Indian scripts) + about 25 display faces | Free | ⑤ |
| Music | local royalty-free loops in `assets/music/` | Free | ⑤ |
| Statistics and optimizer | `numpy`, `scipy`, `pandas` | Free | ⑦ ⑧ |
| Database | SQLite via `sqlmodel` | Free | all |
| Dashboard | Streamlit + Plotly | Free | all |

---

## 2. The full flow

```
 APP BRIEF  (name, features, audience, languages, brand tone, claims to avoid, optional Play Store link)
    │         └─ Play Store link → google-play-scraper: description, ~200 reviews, screenshots, icon
    ▼
 ①  MARKET RESEARCH ─ Gemini seed searches → YouTube autocomplete (+ pytrends) → demand score
    │                 → YouTube Data API: Indian Shorts, last 180 days   + Apify: Instagram Reels by hashtag
    │                 → creator baselines → outlier score → Gemini watches the top Shorts → patterns
    │                 → MARKET PLAYBOOK (recipes, hook templates, angles, avoid list, content gaps)
    ▼
 ②  UNDERSTAND ────── Gemini: personas, pain points, real user phrases, key messages, Indian moments
    ▼
 ③  CONCEPTS ──────── Gemini: 10 ideas = persona × pain × message × India scenario × format
    ▼                 (≥ 60% built on a proven market pattern, with the evidence stored)
 ④  VARIATIONS ────── concepts × hooks × languages × creator styles ≈ 300 tagged creatives
    │                 → scored → best 60 get full scripts → safety check → 12 picked to film
    ▼
 ⑤  RENDER (fast) ─── edge-tts voice + Pexels footage / app screens + adaptive captions + music
    │                 → Remotion → 1080×1920 MP4 + designed thumbnail            (free, ~45 s each)
    │
 ⑤b PRODUCTION CREW (for the videos that matter)
    │   OWNER → DIRECTOR (scene plan) → MAGNIFIC (natural voice, AI images/clips) → EDITOR (v1)
    │     → REVIEWERS: text · visual · audio · brand → QC → tasks back to the right role → EDITOR (v2…)
    │     → OWNER approves (final export) or sends back with a note
    ▼
 ⑥  TEST ──────────── post + import real numbers (CSV / manual), or the SIMULATED market for demos
    ▼
 ⑦  INSIGHTS ──────── lift + probability best per language / hook / format / style / message; Gemini narrates
    ▼
 ⑧  NEXT BATCH ────── 70% exploit (variations of winners) + 30% explore → back to ⑤  (Gen 2, 3, …)
                      Market research refreshes weekly; "Market vs. us" compares the two.
```

The rest of this section walks through each step: what it does, which tools it uses, and what it produces.

### Brief — the input
A JSON brief (`data/sample_briefs/*.json`, or the **Brief** page) with the app name, one-liner, features, USP,
audience, city tiers, languages, brand tone, CTA and **claims to avoid**.
- **With a Play Store link**, `google-play-scraper` (country `in`) adds the store description, about 200 reviews
  (English and Hindi) and the real screenshots and icon. The reviews supply real user phrases.
- **Without one**, put screenshots in `data/apps/<app_id>/screenshot_*.png` and `icon.png`.

Sample briefs: `fintech.json` (PaisaPal), `edtech.json` (PadhoPlus), `cashcalc.json` (Cash Calculator),
`motorinsurance.json` (Motor Insurance Calculator).

### ① Market research — "what's working now"
Code: `engine/research/`. The aim is to make Gen 1 an informed guess instead of a blind one.

1. **Demand keywords** (`keywords.py`)
   - **Gemini** turns the brief into about 30 *problem-style* searches per language ("IDV kya hota hai",
     "bike insurance kitne ka hota hai"), plus Instagram hashtags.
   - **YouTube autocomplete** expands each seed (unofficial endpoint: cached, throttled to one call per second,
     skipped silently if it fails). Suggestions that appear for several seeds are strong demand.
   - Optional **pytrends** adds Google Trends interest.
   - Each keyword gets a 0–1 demand score, and the top 20 are searched.
2. **Discover Shorts** (`discover.py`) with the **YouTube Data API v3**:
   - `search.list` per keyword (region India, Shorts, last 180 days, by views): 100 units per call.
   - `videos.list` for stats and duration (≤ 180 s, ≥ 3,000 views).
   - A relevance filter using brief-derived terms (word match for Latin script, substring for Indian scripts).
   - Quota is tracked and the run stops cleanly before `YOUTUBE_DAILY_QUOTA_BUDGET`.
3. **Discover Reels** (`instagram.py`) with **Apify**:
   - `apify/instagram-hashtag-scraper` finds Reels for about 12 problem hashtags.
   - `apify/instagram-reel-scraper` pulls each creator's recent Reels for their baseline.
   - Optional transcripts (`APIFY_TRANSCRIPTS=1`).
   - Every actor run has a hard dollar limit (`maxTotalChargeUsd`), plus the monthly cap `APIFY_MONTHLY_BUDGET_USD`.
4. **Outlier score** (`scoring.py`)
   - Views alone mislead, so each video is compared with *its own creator's* median views. `channels` +
     `playlistItems` give the last 30 uploads; the fallback is lifetime average.
   - `outlier = views / baseline`. For example, 50K views on a channel that usually gets 2K is a 25× outlier.
   - `market_score` = 55% outlier rank + 25% views-per-day rank + 20% engagement rank.
   - The top 25 are kept, at most 2 per creator.
5. **AI analysis** (`analyze.py`)
   - **Gemini watches each top Short by its YouTube URL** and tags it with the *same labels the engine uses to make
     videos*: hook type, format, language, closest creator style, emotional trigger, CTA, length. It also records
     the angle, the message and why it worked.
   - Reels are analysed from caption, hashtags and transcript. Videos are never downloaded.
   - Invalid tags get one retry, then fall back to "other".
6. **Patterns** (`patterns.py`)
   - Per tag: share, performance-weighted share, average outlier, lift.
   - Top combinations and topic clusters.
   - Fewer than 3 videos counts as a "weak signal".
7. **Market Playbook** (`playbook.py`)
   - Gemini writes 3 proven recipes, 10 fill-in-the-blank hook templates, topic angles, an avoid list and content gaps.
   - It may only cite numbers from the pattern tables; lines with invented numbers or copied hooks are dropped.

**Used later by:**
- **understand** — demand questions become pain points.
- **concepts** — must build on proven patterns.
- **scripts** — follow the recipes and hook templates.
- **scoring** — adds a market prior.
- **optimizer** — starts from the market patterns, worth at most 5 creatives of evidence.
- **insights** — the "Market vs. us" comparison.

**Data rules:** metadata plus our own analysis only; `cli.py research --purge-stale` deletes YouTube data older
than 30 days; compliance flags any script line that copies a stored market hook.
**Measured cost per run:** about 2,000–2,500 of the 10,000 free YouTube units, about ₹18 Gemini, about $1 Apify.
Re-running within 7 days uses the cache and costs nothing.
→ Dashboard: **Market Research**

### ② Understand the app
Code: `engine/understand.py`. **Gemini** reads the brief, the reviews and the Market Playbook, and writes:
- 3–4 personas (age, city tier, goal, frustration)
- pain points with evidence phrases
- loved features and real user phrases
- 5–8 key messages
- Indian moments for this month and next, from `config/india_context.yaml` (festivals, exams, salary day, IPL, …)

→ Dashboard: **Understand**

### ③ Concepts
Code: `engine/concepts.py`. **Gemini** writes about 10 video ideas. Each pairs a persona, a pain and a message with a
specific Indian scene ("month-end UPI balance check before chai"). Each also gets one of 9 formats from
`config/formats.yaml`: POV, problem-solution, three reasons, before-after, old way vs new way, myth vs fact, …
At least 60% must build on a proven market pattern and store `market_evidence`.

### ④ Variations, scripts and safety
Code: `engine/variations.py`, `scripts.py`, `localize.py`, `compliance.py`.
- **Hooks:** about 5 opening lines per concept per language, each a different hook type (question, bold claim,
  relatable pain, curiosity gap, number list, desi humour, trend moment). Written natively, never translated:
  - Hinglish in Roman script, Hindi in Devanagari, Tamil in Tamil script, and so on.
  - A line in the wrong script is rejected.
- **Matrix:** concepts × hooks × languages × creator styles, for example 10 × 5 × 3 × 2 ≈ **300 tagged creatives**.
  Near-duplicate hooks are removed.
- **Creator styles** (`config/creator_styles.yaml`, all faceless): bestie narrator, explainer didi/bhaiya,
  meme text, screen demo, storytime. Each sets voice gender, speed, cut pace, music energy and screenshot share.
- **Score:** Gemini rating + hook heuristics (short? number? question? names a pain?) + market prior.
- **Scripts:** the best 60 get full 20–35 s scene-by-scene scripts in strict JSON. The 60 are spread across languages,
  styles and hook types, not just top scores.
- **Safety:** regex rules, then a Gemini review. They flag:
  - fake testimonials and invented statistics
  - guaranteed outcomes and earning promises
  - finance or health promises
  - religious or political content
  - your claims to avoid
  - lines copied from other creators

  Flagged scripts wait for a human.
- **Pick 12** to film, again stratified. Each gets a tracking slug and a Play Store UTM link.

→ Dashboard: **Generate**, **Video Ideas**

### ④b Video modes and characters — decided per app by its research
Every creative is shot in one of four **visual modes**. The mode is a tag, just like language or hook type, so the
insights and the growth loop learn which mode wins *for each app*.

| Mode | What it is | Main sources | Typical Magnific credits |
|---|---|---|---|
| **faceless** | narrator over footage | stock video, app screens, motion graphics (steps · number · old vs new) | ~100 (voice) |
| **images_vo** | stills with big per-scene headlines + narrator | Pexels photos, AI scene photos, app screens, motion graphics | ~250 |
| **character** | the app's recurring character *acts* in the scenes; a narrator speaks | character photos + one animated clip, stock, app screens | ~450 |
| **ai_character** | the presenter character *talks to camera* for the hook and the CTA (lip-synced), character footage in between | talking clips (≈180 credits per second), character photos, app screens | ~1,400 |

- **The mix comes from each app's own research** (`engine/modes.py`).
  - Gemini tags each over-performing market video's production style. The styles map to our modes: talking head →
    AI character, skit → character, image slideshow → images + voice-over, text on b-roll / screen demo → faceless.
  - The weighted shares become that app's target mix. Every mode keeps a small floor so it can still be discovered.
  - The mix is then fitted to the remaining Magnific credits: expensive modes step down to their next-best
    compatible mode.
  - Without research, the format decides (e.g. POV → character, three reasons → images + voice-over).
  - Creator styles limit what fits (a screen-demo style is never a character video).
- **Characters per app** (`engine/production/personas.py`):
  - Gemini casts 1–2 **protagonists** from the app's own audience research and 1 **presenter** matching its brand tone.
  - Magnific generates 4 candidate portraits each, and you pick a face on the Production Crew page.
  - The face becomes a Magnific **character-library** entry named `<app>_<character>`, so every later photo, clip and
    talking video shows the same person.
  - Characters are always fictional, never testimonials.
- **The director plans by mode.**
  - Sources are decided up front: stock only for generic settings; AI photos for specific moments; character
    photos, clips and talking only where the mode allows; app screens (or a motion graphic when the app has none)
    for what the app does.
  - Talking lines are kept to ≤ 9 words (lip-sync is billed per second).
  - Magnific jobs are chained (portrait → clip; voice line → talking video) and capped per mode. Anything over the
    cap is downgraded, never overspent.
- **Routing:** faceless and images + voice-over videos render immediately for free. Character and AI-character
  videos go to the production crew. In simulation all four are tested, so the loop learns every mode.

### ④c Every video has its own look
Each video draws its own **text look**:
- **Fonts:** a Latin font plus one for its script, from about 30 faces in `config/typography.yaml`.
- **Caption layout:** karaoke line · one big word · boxed line · lower-third bar.
- **Headline style:** sticker · banner · outline · marker underline · two-colour split.
- **Hook style:** tilted card · underline · stacked.
- **Plus:** size, outline, animation and cut transition.
- **Colours:** a palette derived from its own footage.

`captions.assign_styles` guarantees that **no two videos of an app's generation share the same look**, while each
video stays consistent inside itself and keeps its look on re-render. Modes steer the choice: talking videos keep
captions low, off the face. Captions on app screens and motion graphics sit in the bottom band.

### New app? One command
```bash
python cli.py onboard --brief data/sample_briefs/<new_app>.json
#   brief → market research → understanding → the app's characters (+ Magnific job sheet) → its video-mode mix
python cli.py crew persona pick <app> <character> <n>    # or pick faces on the Production Crew page
python cli.py generate <app>                             # ideas tagged with modes and looks
python cli.py render <app>                               # free modes render; character modes go to the crew
python cli.py crew batch-jobs                            # one Magnific sheet across all apps and runs
```

### ⑤ Render — the fast, free path
Code: `engine/render.py`, `tts.py`, `visuals.py`, `captions.py`, `textfit.py`, `remotion/`.

1. **Voice:** **edge-tts** speaks each scene with word timings. The voice is picked by language and creator style
   (e.g. `hi-IN-MadhurNeural`, `ta-IN-PallaviNeural`); gTTS is the fallback. If the voice runs long, it speeds up
   to fit about 36 s.
2. **Footage:** **Pexels** (portrait video first, then photo) → **Pixabay** → gradient. Queries prefer
   Indian-looking results. App screenshot scenes sit in a phone frame over a blurred copy, with a slow zoom.
   Attribution is saved.
3. **Text that fits the footage** (`textfit.py`):
   - Frames of every scene are sampled and captions go in the calmest band, clear of the subject and platform UI.
   - Each video gets its own palette (accent colour from the footage plus a text-plate style) and its own font
     pairing, size and animation (`config/typography.yaml`), kept consistent within the video.
4. **Assembly** (**Remotion**, `remotion/src/UgcVideo.tsx`):
   - Python writes a props file (scene clips, voice tracks, caption timings, hook, end card) and
     `remotion/render.mjs` renders it.
   - The video gets cuts with push-ins, word-highlight captions, a hook sticker in the first 2 s (works on mute),
     a music bed about 20 dB under the voice (mix near −14 LUFS), and a 2 s end card with icon, app name and CTA.
   - A designed **thumbnail** (Remotion `Cover`) is rendered and embedded as the MP4 cover.
   - Chromium shapes Indian scripts correctly.
5. **Output:** `outputs/<app>/gen_<n>/<creative_id>/` holds `video.mp4`, `thumbnail.jpg`, `script.json`, and
   `caption.txt` (post caption, hashtags, tracking link, stock credits). Each video takes about 45 s on a laptop.

→ Dashboard: **Studio**

### ⑤b Production crew — Magnific, director, editor, reviewers, QC, owner
Code: `engine/production/`, prompts `engine/prompts/crew_*.md`, settings `config/production.yaml`.
Use it for videos that must look and sound professional.

```
OWNER ──► DIRECTOR ──► MAGNIFIC ASSETS ──► EDITOR (v1)
                                               │
              ┌─────────── REVIEWERS ◄─────────┘
              │  text/caption · visual/design · audio · brand/style
              ▼
             QC ── fail ──► each issue = a task for the role that fixes it ──► EDITOR (v2, v3 …)
              │
            pass
              ▼
            OWNER ── approve ──► FINAL EXPORT
              └──── send back with a note ──► DIRECTOR turns it into fixes ──► EDITOR …
```

**The roles**

| Role | What it does | Tools |
|---|---|---|
| **Owner** (you) | Hands over the script. At the end, **approves** (the version becomes the creative's final video) or **sends it back** with a plain-language note. | Dashboard / CLI |
| **Director** | Turns the script into a scene plan: shot per scene (stock, app screen, or AI clip for the hook), footage query, caption position, pace, transitions, a one-word **delivery cue** per line (excited, annoyed, reassuring…) and voice direction. Decides which Magnific assets are needed, within the credit caps. | Gemini |
| **Magnific assets** | Natural ElevenLabs voice for every line (voices cast per language and creator style); an AI image when stock fails a scene twice; optionally an AI hook clip. | **Magnific** (MCP) |
| **Editor** | Cuts v1, v2 … in Remotion from the plan, and applies every fix precisely. | Remotion, faster-whisper |
| **Text / caption reviewer** | Spelling, grammar, correct script, hook ≤ 6 words, caption timing (flashing lines), text cut off or covering faces or phones, missing captions. | measured checks + Gemini looking at frames |
| **Visual / design reviewer** | Footage matches the scene and looks Indian; **app screens belong to this app** (a wrong-app screen is a blocker); cropping, quality, repeated clips, layout. | Gemini looking at frames |
| **Audio reviewer** | Loudness (target −14 LUFS) and true peak, silences inside the video, robotic free voice, mispronounced words, music level and mood. | ffmpeg EBU R128 + silencedetect, Gemini listening |
| **Brand / style reviewer** | Claims to avoid, fake testimonials, invented numbers, app name, CTA, tone vs brand tone, thumbnail. | compliance rules + Gemini |
| **QC** | File checks (1080×1920, 30 fps, 18–42 s, audio, cover art). Scores the version (100 − 25 per blocker − 10 per major − 3 per minor), passes it only with no blocker/major issue and score ≥ 80, and files every issue as a task. | ffprobe |

**Every issue is a specific, timestamped task with a fix the Editor can apply.** For example:

| When | Issue | Raised by | Fixed by | Fix |
|---|---|---|---|---|
| 00:20.4 | "ऐप" (app) is pronounced "आप" (you) | Audio | Text defines the respelling | `set_text` → line re-recorded in Magnific |
| 00:10.5 | Screenshot shows a different app | Visual | Director | footage instead; app screenshots blocked for this video |
| 00:05.7 | Street traffic instead of a shopkeeper writing accounts, twice | Visual | Assets | `request_asset image` → Magnific photo |
| — | Loudness −6.7 LUFS | Audio | Audio | `set_mix` |
| 00:23.4 | Caption covers the phone the man is holding | Text | Editor | `move_text` |

Fix types: `set_text`, `set_visual`, `move_text`, `set_design`, `set_pace`, `set_mix`, `request_asset`.
Anything else goes to a human. The crew revises up to `max_revision_rounds` (3) times, then the owner decides.

**How Magnific is used (Premium plan, MCP only).** The Python engine can't call Magnific directly, so:
1. The crew writes `production/magnific_jobs.json` with the exact Magnific tool calls (`audio_tts`,
   `images_generate`, `video_generate`) and the estimated credits.
2. Claude runs them through the **Magnific MCP**.
3. The results are imported with `cli.py crew import …`, which logs the actual credits in the ledger.

Caps: `monthly_credit_cap` (18,000) and `per_video_credit_cap` (450). AI images never show a phone screen or app
interface; only real screenshots may show the product.
Voices: Hindi and Hinglish use Saurabh Sharma, Anjali Bhardwaj, Shreya Malhotra and Kabir Sethi; Tamil uses Swetha
Devi, Suresh Sundaram and others. Telugu, Marathi and Bengali keep edge-tts until a native voice is tested.

**A real run** (Cash Calculator, Hindi, meme style): v1 scored 0 → v2 57 → v3 90 → v4 → v5 → **v6 scored 100 and was
approved**. It used 7 Magnific voice lines and 3 Magnific images (254 credits) and 25 Gemini calls (₹14.85).

→ Dashboard: **Production Crew** (stage tracker, versions side by side, tasks with who raised / who fixes /
timecode, crew log, Magnific jobs, Approve / Send back)

### ⑥ Test
Code: `engine/tracking.py`, `metrics.py`, `simulator.py`.
- **Tracking:** every creative has a slug and a Play Store link with a UTM `referrer`
  (`utm_content=<slug>`, `utm_campaign=<app>_gen<n>`), so installs trace back to the exact video.
- **Real data:** post the videos, then type numbers in or import a CSV (`slug` column): impressions, views, 3-second
  views, watch %, likes, comments, shares, saves, clicks, installs, spend, D1/D7.
- **Simulated market** (for demos): hidden "true" effects per tag produce realistic funnel numbers in seconds. They
  are always stored and shown as **SIMULATED**, and the optimizer never sees the hidden effects.
- **Derived metrics:** hook rate, average watch %, engagement rate, CTR, install rate, **installs per 1,000 views**
  (the primary metric), CPI (₹0 when organic), D1/D7 retention.

→ Dashboard: **Performance**

### ⑦ Insights
Code: `engine/insights_perf.py`. Statistics first, AI second.
- For every tag: the primary metric, **lift** vs the average, and a Bayesian **probability of being best**
  (Beta posteriors, 5,000 draws). The same for combinations such as language × hook type.
- Nothing is called a winner below 3 creatives and 500 views ("needs more data").
- **Gemini** then writes 3–5 findings, recommendations and "stop doing" items. Any line that cites a number not in
  the statistics is dropped.
- A **Market vs. us** table shows where our winners agree or disagree with the market's patterns.

→ Dashboard: **Insights**

### ⑧ Next batch — the growth loop
Code: `engine/optimizer.py`, `loop.py`.
- A joint model (ridge-penalised Poisson over all tags) estimates each tag's effect *together*, so a format isn't
  credited for a winning hook it happened to be paired with. It starts from the market patterns, but our own data
  overrides them within a couple of generations.
- **Thompson sampling** allocates the next batch:
  - **70% exploit:** new hooks for the top 20% creatives, plus new concepts on the winning message.
  - **30% explore:** under-tested combinations.
- Winners, losers and recommendations go to Gemini as guidance.
- The new batch goes back to ⑤: Gen 2, Gen 3, and so on. `cli.py loop --generations 3 --simulated` runs it
  unattended, and a truth check shows whether the loop found the simulator's hidden best values.

→ Dashboard: **Growth Loop**

### Cost and control
Code: `engine/cost.py`, `engine/production/assets.py`.
- **Every step** is logged with seconds and cost.
- **Gemini:** actual ₹ from token usage, capped per day by `GEMINI_DAILY_BUDGET_INR`. Billing errors are never retried.
- **Apify:** actual $ per run, capped per run and per month.
- **Magnific:** actual credits per imported asset, capped per video and per month.
- **Caching:** every external call (LLM, TTS, stock search, YouTube, Apify, analyses, Magnific imports) is cached
  on disk, so re-running costs nothing for work already done.

→ Dashboard: **Economics** (actual spend, cost and time per video, videos per hour vs a traditional creator
workflow, using `CREATOR_REEL_COST_INR` and `CREATOR_TURNAROUND_DAYS`)

---

## 3. What it costs (measured, September 2026)

| Item | Cost |
|---|---|
| Market research run (per app, reused for 7 days) | ~₹18 Gemini + ~$1 Apify + free YouTube quota |
| Ideas for a whole generation (~300 hooks, 60 scripts, safety) | ~₹50 Gemini (≈ ₹0.8 per script) |
| Fast-path video (edge-tts + stock + Remotion) | ₹0 (≈ 45 s of laptop time) |
| Production crew: Gemini director + 4 reviewers per version | ≈ ₹2.5 per version |
| Magnific voice line (ElevenLabs eleven_v3) | 12–18 credits (≈ 90–110 per 30 s video) |
| Magnific scene image (Seedream 4) | 50 credits |
| Magnific hook clip (Kling 2.5, 5 s, 1080p) | 325 credits (Seedance Fast 720p: 1,175) |
| A crew video that passes in 1–2 rounds | ≈ ₹5 Gemini + ~100 credits voice (+ 50 per AI image if stock fails) |

On Magnific Premium, 20,000 credits cover about 190 videos a month with natural voice only, or about 70 with voice
plus three AI images each. For comparison, a creator reel is ₹10,000 in the default settings.

---

## 4. Setup

```bash
# 1. Python 3.11+
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -r requirements.txt            # or: pip install -r requirements.txt

# 2. Node.js 18+ and Remotion (renders the videos)
#    macOS: brew install node      Ubuntu: sudo apt install nodejs npm
cd remotion && npm install && cd ..           # the first render downloads Chrome Headless Shell (~100 MB)
#    Remotion is free for individuals and companies of up to 3 people; larger teams need a licence
#    (https://www.remotion.dev/license).

# 3. ffmpeg (loudness/silence checks, frame grabs, cover art, placeholder images, music loops)
#    macOS: brew install ffmpeg-full    Ubuntu: sudo apt install ffmpeg    (or set FFMPEG_BIN)

# 4. Fonts + music (one-time)
python scripts/get_fonts.py                   # Noto (all Indian scripts) + display fonts
python scripts/make_music.py                  # 3 royalty-free loops

# 5. Keys
cp .env.example .env
```

| Key | Where to get it | Used for |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com/apikey | all AI writing, video analysis, crew reviewers (paid billing OK; `GEMINI_DAILY_BUDGET_INR` caps it) |
| `GROQ_API_KEY` | console.groq.com/keys | fallback LLM (optional) |
| `YOUTUBE_API_KEY` | Google Cloud console → enable "YouTube Data API v3" → Credentials | market research (free 10,000 units/day) |
| `APIFY_API_TOKEN` | console.apify.com/account/integrations | Instagram Reels research (`APIFY_MONTHLY_BUDGET_USD` caps it) |
| `PEXELS_API_KEY` | pexels.com/api | stock footage |
| `PIXABAY_API_KEY` | pixabay.com/api/docs | stock fallback (optional) |
| **Magnific** | no key: in Claude Code run `claude mcp add --transport http magnific https://mcp.magnific.com`, then `/mcp` to log in | production crew voices, images, clips |

Without any LLM key, the whole pipeline still runs offline with `LLM_PROVIDER=mock` (template copy, for tests only).

---

## 5. Running it

**Dashboard:** `streamlit run app.py` → pick an app in the sidebar → **Home → Run Full Loop (Demo)**. Long jobs run
in the background with a live step-by-step tracker, and only one run per app is allowed at a time. Restart the server
after code changes (the file watcher is off).

**Command line**

```bash
# research and ideas
python cli.py research --brief data/sample_briefs/cashcalc.json   # ① (cached 7 days)
python cli.py research --purge-stale                               # delete YouTube data older than 30 days
python cli.py understand --brief data/sample_briefs/cashcalc.json # ②
python cli.py generate cashcalc [--languages hi,ta] [--fresh]      # ②–④: ideas only (no videos)

# fast videos, test, learn
python cli.py render cashcalc [--again]                            # ⑤
python cli.py simulate cashcalc                                    # ⑥ SIMULATED metrics
python cli.py import-csv metrics.csv                               # ⑥ real metrics (slug column)
python cli.py insights cashcalc                                    # ⑦
python cli.py next cashcalc                                        # ⑧ Gen n+1
python cli.py loop --brief data/sample_briefs/fintech.json --generations 3 --simulated
python demo.py                                                     # judge demo end to end
python cli.py economics cashcalc

# production crew (⑤b)
python cli.py crew start <creative_id>             # director → Magnific job sheet (or --skip-assets)
python cli.py crew jobs <run_id>                   # the Magnific calls still to run
python cli.py crew import <run_id> <request_id> <file-or-url> --credits N
python cli.py crew resume <run_id>                 # editor → reviewers → QC → revisions → owner
python cli.py crew status <run_id>                 # tasks (raised by / fixed by / timecode) + crew log
python cli.py crew approve <run_id> [--note "…"]   # final export
python cli.py crew reject <run_id> --note "…"      # back to the director → new version

pytest -q
```

To produce a crew video with Magnific, ask Claude Code: *"start the crew for creative 358 and run the Magnific jobs"*.
Claude runs the job sheet through the Magnific MCP, imports the results and resumes the crew.

---

## 6. Dashboard pages

| Page | What you see |
|---|---|
| **Home** | D1–D8 deliverables checklist, app picker, **Run Full Loop (Demo)** |
| **Brief** | form or sample brief, Play Store enrichment |
| **Market Research** | demand keywords, top Shorts/Reels with outlier ×, pattern charts, winning combinations, Market Playbook, quota used |
| **Understand** | personas, pain points, user phrases, key messages, Indian moments |
| **Generate** | variation matrix counts, creatives with tags / score / safety status, approve / edit / reject |
| **Video Ideas** | idea cards with scripts and players, downloads |
| **Studio** | render queue, video grid with thumbnails, captions and tracking links |
| **Production Crew** | Magnific credit meter, stage tracker, versions, review tasks, crew log, Magnific jobs, owner Approve / Send back |
| **Performance** | real / simulated toggle, KPI cards, breakdowns by tag, leaderboard, manual entry, CSV import |
| **Insights** | lift + probability best tables, AI findings, top and bottom creatives, Market vs. us |
| **Growth Loop** | explore/exploit allocation, Generate Next Batch, Gen-over-Gen chart, lineage, hidden-truth check |
| **Economics** | actual spend, time per video, throughput, engine vs traditional creators |

---

## 7. Project structure

```
ugc-growth-engine/
├── README.md  PLAN.md  requirements.txt  .env.example
├── app.py                         # Streamlit dashboard (12 pages)
├── cli.py                         # command line (incl. `crew` sub-commands)
├── demo.py                        # judge demo
├── config/
│   ├── languages.yaml             # language → edge-tts voices, Noto font, script, style notes, regions
│   ├── india_context.yaml         # month → festivals/moments + everyday Indian scenarios
│   ├── formats.yaml               # 9 UGC formats + beat sheets
│   ├── creator_styles.yaml        # 5 faceless creator styles (voice, captions, cuts, music, animations)
│   ├── typography.yaml            # font pools per script (each video draws its own pairing)
│   ├── taxonomy.yaml              # hook types, CTA types, length buckets
│   ├── research.yaml              # market research limits (YouTube, Apify), priors, refresh/stale windows
│   └── production.yaml            # crew: revision rounds, QC pass score, Magnific caps, voice casting, costs
├── engine/
│   ├── settings.py db.py cache.py cost.py locks.py progress.py
│   ├── llm.py                     # Gemini → Groq → Ollama (→ mock); JSON repair; media (frames/audio) calls
│   ├── prompts/*.md               # every prompt, editable (incl. research_* and crew_*)
│   ├── research/                  # ① keywords, discover (YouTube), instagram (Apify), scoring, analyze,
│   │                              #   patterns, playbook, market (priors/guidance), run (orchestrator, purge)
│   ├── brief.py understand.py concepts.py scripts.py localize.py variations.py compliance.py
│   ├── tts.py visuals.py captions.py textfit.py render.py tracking.py
│   ├── production/                # ⑤b crew: common, director, assets (Magnific), editor, reviewers, qc, pipeline
│   ├── metrics.py simulator.py insights_perf.py optimizer.py loop.py
│   └── mock_content.py            # offline templates for LLM_PROVIDER=mock
├── remotion/                      # React video: src/UgcVideo.tsx (video), src/Cover.tsx (thumbnail), render.mjs
├── scripts/                       # get_fonts.py, make_music.py
├── assets/fonts/  assets/music/
├── data/sample_briefs/            # fintech, edtech, cashcalc, motorinsurance
├── data/apps/<app_id>/            # screenshots + icon per app
├── reference/youtube_content_engine.ipynb   # original notebook the research logic comes from
├── tests/                         # pytest suite (runs offline with the mock provider)
├── cache/                         # every external call (LLM, TTS, stock, YouTube, Apify, analyses)
├── engine.db                      # SQLite
└── outputs/<app>/gen_<n>/<creative_id>/
        video.mp4  thumbnail.jpg  script.json  caption.txt
        production/  v1/ v2/ … (each version + props + frames)  assets/ (Magnific files)  magnific_jobs.json
```

---

## 8. Guardrails

- **Honest content:** creator, POV, narrator or explainer voice only. No fake testimonials, invented numbers,
  guaranteed outcomes, or claims from the brief's avoid list. Enforced by prompt rules, the compliance check and the
  crew's brand reviewer.
- **No fake product UI:** AI images never draw an app or store interface, and only the app's real screenshots show
  the product. If screenshots show another app, the visual reviewer blocks them.
- **AI disclosure:** the on-video "AI-generated" tag is off by default at the owner's request (`SHOW_AI_LABEL=1`
  turns it on). `caption.txt` always includes `#AIGenerated`. Switch on the platform's own AI-content disclosure
  when publishing.
- **Simulated data** is stored as `source="simulated"` and labelled **SIMULATED** everywhere.
- **Other people's content:** official YouTube API for YouTube stats, Apify for Instagram metadata, no video
  downloads, YouTube data purged after 30 days, copied hooks flagged.
- **Spend:** Gemini daily ₹ cap, Apify monthly $ cap plus a per-run limit, Magnific per-video and monthly credit caps.
  Every paid call is in the ledger.
- **Stock credits** are saved in `caption.txt` and on the video record.

---

## 9. Known limits

- **Magnific is MCP-only:** a crew run pauses at `waiting_assets` until Claude runs the job sheet. With a Magnific
  API key this could be automated.
- **Voices:** Telugu, Marathi and Bengali have no native Magnific voice cast yet, so they use edge-tts.
- **Instagram:** Reels are analysed from caption, hashtags and transcript, because Gemini can't open Instagram URLs.
- **Remotion licence:** free only for individuals and companies of up to 3 people.
- **First run downloads:** the first render fetches Chrome Headless Shell (~100 MB) and the first imported voice
  fetches the Whisper model (~145 MB).
