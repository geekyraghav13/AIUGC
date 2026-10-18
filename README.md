# AI UGC & Social Growth Engine

A tool that turns **one app brief** into a steady stream of short vertical marketing videos (Reels / Shorts),
tests them, learns what wins, and makes a better next batch.

The engine lives in `ugc-growth-engine/`. Its full manual is `ugc-growth-engine/README.md`.

## The question it answers

Can AI-generated UGC content become a repeatable, low-cost growth lever for our apps?

## How it works

```
App brief (or Play Store link)
  → Market research   what people search, which short videos over-perform, and why
  → Understanding     personas, pain points, real user phrases, key messages
  → Scripts           10 per batch, tagged by language, hook, format and style
  → Safety check      no fake testimonials, invented stats or guaranteed outcomes
  → Videos            voice, captions, stock or real screenshots, music, end card (1080×1920)
  → Review and QC     automatic quality score, then the owner approves or sends back
  → Performance       real or clearly labelled simulated metrics
  → Insights          which hooks, languages and formats win
  → Next batch        70% variations of winners, 30% exploration
```

## What you get

- **Market Research:** demand keywords, outlier videos, proven formats and a Market Playbook.
- **AI UGC generator:** hooks, scripts, captions and finished videos.
- **Localisation:** each language is written natively (English, Hindi, Hinglish, Tamil, Telugu, Marathi, Bengali),
  not translated.
- **Variations:** many combinations from one brief, with the best ones rendered.
- **Performance dashboard and AI insights:** lift and probability-best per tag.
- **Growth loop:** generation-over-generation improvement.
- **Economics:** cost and time per video.

## Run it

```bash
cd ugc-growth-engine
.venv/bin/streamlit run app.py --server.port 8501
```

Open http://localhost:8501 (the **Engine** page). Restart after any code change.

The owner's flow:

1. Choose or create an app.
2. Press **Go**: research, understanding, then 10 scripts.
3. Approve the scripts.
4. Videos are produced one at a time on the production board.
5. Approve, send back with a note, or download (one video or all as a zip).

Same flow from the terminal:

```bash
python cli.py onboard --brief data/sample_briefs/<app>.json   # new app: brief → research → characters
python cli.py go <app_id>                                      # research → understanding → 10 scripts
python cli.py batch approve <N>                                # approve the batch
python cli.py batch run <N>                                    # produce the videos
```

## Rules

- **Cost:** free tools by default (free stock, real screenshots, edge-tts voice). Magnific is OFF by default;
  a paid run needs a yes on the exact job list. Gemini and Apify are capped and logged.
- **Languages are per app,** set in the brief (`languages`, `languages_strict`). Check before approving scripts.
- **Generic:** any new app works from its brief alone, with no per-app hand setup.
- **App screens:** only real screenshots, never generated app UI.
- **Honesty:** no fake testimonials, no invented numbers, no guaranteed-outcome claims.
  Simulated data is always labelled SIMULATED, and `caption.txt` carries the `#AIGenerated` disclosure.
- **One run at a time** per app, with live progress.

## Where things are

| What | Where |
|---|---|
| Engine code | `ugc-growth-engine/engine/` |
| Dashboard and CLI | `ugc-growth-engine/app.py`, `cli.py` |
| App briefs | `ugc-growth-engine/data/sample_briefs/` |
| Settings and caps | `ugc-growth-engine/config/`, `.env` |
| Finished videos | `ugc-growth-engine/outputs/<app_id>/` |
| Project handover and spec | `CLAUDE.md` |
