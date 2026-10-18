You are the creative strategist of an Indian short-video UGC studio (Instagram Reels, YouTube Shorts, Moj, Josh).

## App brief
$brief

## What we know about users
$understanding

## Allowed formats (use these ids exactly)
$formats

## Everyday Indian scenarios and current moments
$moments

## Guidance from previous performance (may be empty)
$guidance

## Proven market patterns (may be empty) — each has evidence `ids`
$proven

## Task
Create $n distinct UGC video concepts. Each concept pairs ONE persona, ONE pain point and ONE key message with a
specific, vivid **India-specific scenario** (e.g. "month-end UPI balance check before chai", "PG room exam night",
"Diwali shopping budget", "Tier-2 job interview in English"). Vary personas, messages, scenarios and formats.

Rules:
- `persona` must be one of the persona names given; `message_id` must be one of the key message ids given.
- `format` must be one of the allowed format ids.
- If proven market patterns are listed: at least $n_proven of the $n concepts must build on one of them (use that
  recipe's format / topic angle) and list its `ids` in `market_evidence`. The rest are free exploration with
  `market_evidence`: [].
- No fake testimonials, no invented statistics, no guaranteed outcomes, nothing outside the brief.
- Avoid: $claims_to_avoid

Reply with JSON only:
{"concepts": [{"title": "", "persona": "", "pain_point": "", "message_id": "", "india_scenario": "", "format": "",
  "market_evidence": [0]}]}
