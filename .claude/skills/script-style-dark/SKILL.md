---
name: script-style-dark
description: Writing rules for "dark" faceless-style videos (mystery, true stories, hidden facts) — narration over B-roll/AI images, avatar optional.
---
- Tone: low, suspenseful, second person ("Você já se perguntou…"). Short sentences, cliffhanger every 3–4 scenes.
- Structure: cold-open hook with the most shocking fact → context → escalation (3–5 beats) → reveal → open question CTA.
- Visuals: mostly `ai_image` (cinematic, dark palette, film grain; describe lighting and camera) and `broll`; `headline` for real news evidence; when `get_project` returns a ready `avatar`, open with `avatar_full` in the hook and close with it in the CTA (the presenter's face builds trust); without one, no avatar scenes.
- Never present speculation as fact; mark theories explicitly ("uma teoria diz…").
- Avoid a slideshow: most scenes need motion — footage, broll video, or an ai_image with a `visual.camera` move (push_in, pull_out, pan_left, pan_right, pan_up, pan_down) that fits the composition. Never more than 2 static images in a row.
- Hold one detail back until the reveal; re-hook with a question every ~20 s in long videos.
- Long videos: hook (the most shocking fact, `hook_type` usually `number` or `best_moment`) → promise → bumper → `blocks` of 60–90 s, each one beat of the escalation (mini question → reveal → "mas isso não é o mais estranho…"); the held-back detail is the last block's reveal, before the ending.
