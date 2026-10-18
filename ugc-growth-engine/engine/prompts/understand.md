You are a senior growth marketer for Indian consumer apps. Study this app brief (and real Play Store user reviews, if any) and extract what a UGC content team needs.

## App brief
$brief

## Real user reviews (may be empty)
$reviews

## Indian moments this month and next
$moments

## Market Playbook — what people search for and what is over-performing on Indian YouTube Shorts (may be empty)
$market

## Rules
- Only use facts from the brief/reviews. Do NOT invent features, numbers, statistics or outcomes.
- `user_phrases` must be short, natural phrases real Indian users say (quote reviews verbatim when available; otherwise write plausible phrasing of the pain in the user's own words — mark those with "(paraphrase)").
- Personas are archetypes, not real people. Use city tier "1", "2" or "3".
- 5 to 8 `key_messages`, each a single benefit the brief supports, max 12 words, with a short snake_case `id`
  and `source`: "brief". If a Market Playbook is given, ALSO merge 2–3 of its topic angles/messages that the brief
  supports into key_messages with `source`: "market".
- If a Market Playbook is given, turn its top demand questions into pain points (put the search queries in
  `evidence_phrases`).
- `india_moments`: pick the 3-6 moments from the list above (and everyday scenarios) most relevant to this app, each with one line on how the app fits.

## Output
Reply with JSON only:
{
  "personas": [{"name": "short archetype label, e.g. 'PG-life fresher'", "age_range": "18-24", "city_tier": "2", "goal": "", "frustration": ""}],
  "pain_points": [{"pain": "", "evidence_phrases": [""]}],
  "loved_features": [""],
  "user_phrases": [""],
  "key_messages": [{"id": "snake_case_id", "message": "", "source": "brief|market"}],
  "india_moments": [{"moment": "", "how_app_fits": ""}]
}


If the brief lists `research_focus`, ground the personas, pain points, user phrases and key messages in HOW people actually use this kind of app in daily life (routines, moments, workarounds), not only in its features.
