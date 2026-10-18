You are the head of content for an Indian consumer app. Using ONLY the market evidence below (computed from
$n_videos over-performing, relevant YouTube Shorts in India), write a Market Playbook for the app.

## App
$app

## Pattern statistics (share = % of videos, weighted_share = share weighted by performance, avg_outlier = × the
## creator's usual views, lift = avg outlier vs the sample average; "weak signal" = fewer than $min_videos videos)
$patterns

## Top combinations (format + hook_type + language)
$combos

## Topic angles (clusters)
$topics

## Best examples (for your understanding only — NEVER copy their lines)
$examples

## Allowed tag values
formats: $formats
hook types: $hook_types
languages: $languages
creator styles: $creator_styles

## Task
- `recipes`: exactly 3 clearly different, proven format recipes. Each = format + hook_type + language + creator_style
  (allowed values only; prefer values with signal "pattern"), a short `name`, `evidence` citing numbers copied exactly
  from the statistics above, and 2 `example_urls` taken from the examples/combos above.
- `hook_templates`: 10 abstracted fill-in-the-blank hook templates using ___ for blanks (e.g. "___ ke baad bhi ___?").
  NEVER copy or lightly edit a creator's actual line.
- `topic_angles`: 5 topic angles to build concepts on (from the clusters above).
- `messages`: 5 core messages that resonate (one line each).
- `narrative`: 4–6 sentences for a marketing team summarising what works now. Use ONLY numbers that appear above,
  copied exactly. Never invent numbers.

Reply with JSON only:
{"recipes": [{"name": "", "format": "", "hook_type": "", "language": "", "creator_style": "", "evidence": "",
  "example_urls": [""]}], "hook_templates": [""], "topic_angles": [""], "messages": [""], "narrative": ""}
