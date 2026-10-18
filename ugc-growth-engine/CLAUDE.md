# UGC Growth Engine: working notes for Claude

Deeper handover and owner rules live in `../CLAUDE.md`; read that first. This file covers running and deploying.

## Run locally
`.venv/bin/streamlit run app.py --server.port 8501` (hot reload is off; restart after code changes).

## Deploy (Google Cloud Run)
- `Dockerfile` and `.dockerignore` are in this folder. The image has Python 3.11, ffmpeg and Noto fonts. It has no Remotion or Chromium, so the cloud demo cannot render video.
- `.env`, `cache/`, `outputs/`, `.venv` and `node_modules` are never copied into the image.
- **Secrets go in Secret Manager**, mounted with `gcloud run deploy --set-secrets`: GEMINI_API_KEY, PEXELS_API_KEY, YOUTUBE_API_KEY, APIFY_API_TOKEN, MAGNIFIC_API_KEY, MAGNIFIC_WEBHOOK_SECRET. Never put them in env vars, the image, or git.
- Non-secret env: `LLM_PROVIDER=gemini`, `GEMINI_DAILY_BUDGET_INR=50`, `USD_INR=88`.
- Region `asia-south1`, service `ugc-growth-engine`, 2 CPU / 2Gi, max 1 instance, unauthenticated (owner asked for no app login).
- Cloud Run disk is ephemeral, so the SQLite DB resets on restart. Bake a snapshot of `engine.db` (made with `sqlite3 engine.db ".backup ..."`) into the image for demo data.
- Magnific stays OFF in `config/production.yaml`. Do not raise credit caps for the cloud demo.

## Tooling on this machine
- `gcloud` is installed via Homebrew; use `export PATH=/opt/homebrew/share/google-cloud-sdk/bin:$PATH`.
- `gh` is logged in as `geekyraghav13`. The project folder is not a git repo yet.
- `gcloud auth login` still needs to be done by the owner (`! gcloud auth login`).
