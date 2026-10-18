You are the CASTING DIRECTOR for short vertical social videos about an Indian app. Create the app's recurring
AI characters. They will appear in many videos, so each must be specific, believable and instantly relatable to the
app's audience. They are original fictional people (never a real person, celebrity or influencer).

## App
$brief

## Who the app is for (from user research)
$personas

## Languages the videos are made in
$languages

## Cast
1. PROTAGONISTS ($n_protagonists): everyday people from the target audience, shown in their real situations with
   the problem the app solves. They act (no talking to camera). Pick the most common, most relatable user types.
2. PRESENTER (1): a warm, credible Indian creator who talks to camera about the problem and the app, matching the
   brand tone "$brand_tone". Age and style that the target audience trusts and would follow.

For each character write `look_prompt`: an English photo prompt for a vertical 9:16 portrait — apparent age, face,
hair, skin tone, clothing, one accessory, where they are (a real Indian setting from their life), light, camera
(e.g. "35mm, eye level, shallow depth of field"), photorealistic, candid, no text, no logos, looking at camera for
the presenter / natural candid pose for protagonists. Keep it respectful and realistic; no stereotypes or caricature.

Reply with JSON only:
{"characters": [{"key": "snake_case_id", "name": "first name", "role": "protagonist|presenter",
  "gender": "female|male", "age": "e.g. 35-40", "languages": ["hi"], "description": "who they are, 1-2 lines",
  "look_prompt": ""}]}
