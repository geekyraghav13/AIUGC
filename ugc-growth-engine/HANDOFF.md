# HANDOFF (written 2026-10-06): read this first after a restart

## Resume the conversation
`cd /Users/user/Documents/AIUGC/ugc-growth-engine && claude --continue`   (or `claude --resume` and pick the latest session)

## Start things again (processes stop on restart)
- Old engine dashboard: `cd /Users/user/Documents/AIUGC/ugc-growth-engine && .venv/bin/streamlit run app.py --server.port 8501 --server.headless true`  → http://localhost:8501
- New engine intel dashboard (optional): `cd /Users/user/Documents/Svolto/AIUGC/ugc-growth-engine && .venv/bin/streamlit run intel_app.py --server.port 8502`

## State
- Old engine default market = TIER1 (US/GB/CA/AU), English. India: `ENGINE_MARKET=IN`.
- App `cashbook_us` (Cash Calculator): brief `data/sample_briefs/cashbook_us.json`, screens `data/apps/cashbook_us/`, 8 videos done (batch 4) in ~/Downloads/CashCalculator_Tier1_Videos/.
- Paid Magnific settings are OFF in config/production.yaml (policy). Raise them only for an approved run.

## Pending
4 AI-character videos WITH lip-sync (owner-approved ~430 credits each). Needs the Magnific web app logged in inside the Claude-controlled Chrome window (it showed logged out). Do not spend until the account and balance are visible.
