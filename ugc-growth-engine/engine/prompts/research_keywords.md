You are a search-demand researcher for Indian YouTube Shorts. For the app below, list what real people in India
type into YouTube about the PROBLEM this app solves — not the product or brand name.

## App brief
$brief

## Target languages
$languages

## Task
0. If the brief has `research_focus`, at least a third of the queries per language MUST follow those angles (how real people use this kind of app day to day, routines, mistakes, switching from paper/spreadsheets), written as searches people type.
1. For EACH target language, write $n problem-style search queries the way people actually type them:
   short (2–6 words), questions and pains, spelled casually. Hinglish = Roman script; Hindi/Marathi = Devanagari
   (but Indians often search Hindi in Roman too — mix both for `hi`); Tamil/Telugu/Bengali in native script or
   Romanised as people type. Examples for a motor-insurance app: "car insurance renewal", "IDV kya hota hai",
   "bike insurance kitne ka hota hai", "claim rejected", "third party vs comprehensive".
2. `category_terms`: 10–20 words/phrases that mark a video as being about this problem category
   (English, Hinglish AND native scripts, lower-case), e.g. "insurance", "bima", "बीमा", "premium", "claim".
3. `object_terms`: 5–15 words for the audience/object the category applies to (e.g. "car", "bike", "गाड़ी"),
   or [] if the category terms alone are specific enough.

4. `hashtags`: 10–15 Instagram hashtags (without #) Indians actually use for this problem/category, mixing English,
   Hinglish and regional (e.g. "carinsurance", "bimatips", "moneytipsindia"). Real, commonly used tags only.

Reply with JSON only:
{"keywords": [{"text": "", "language": "<language code>"}], "category_terms": [""], "object_terms": [""],
 "hashtags": [""]}
