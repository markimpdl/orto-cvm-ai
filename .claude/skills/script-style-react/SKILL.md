---
name: script-style-react
description: Writing rules for react/commentary videos over a source clip — the avatar comments on short excerpts of trending content.
---
- Put the source video URL in `visual.source_url` with `type: source_clip`; each clip excerpt ≤ 10 s, total source time < 30% of the video.
- Alternate: clip excerpt → avatar reaction/analysis (`avatar_pip` over frozen frame or `avatar_full`). Commentary must add opinion, context or facts (transformative use).
- Credit the creator on screen (`on_screen_text`) at the first clip.
- Hook: the most surprising moment of the clip + a strong opinion.
- Keep only the source's strongest moments; what you add (context, data, a clear opinion) must dominate the runtime (transformative use).
- Long videos: each clip excerpt + its commentary forms a 60–90 s `block` (question the clip raises → your take → continuity line into the next clip).
- When the source TEACHES something (tip, guide, how-to, mistake to avoid), our video teaches it too: the viewer leaves
  knowing the actual steps/answer, explained by the presenter. Never stop at the teaser and send people to the
  creator's channel for the answer (credit him, but the value is in our video). Such Shorts are not "curtinhos":
  aim for 45–58 s so the lesson fits (user's rule, 2026-11-22).
- Source in ANOTHER language than the channel (e.g. an English tutorial on the pt-BR channel): never play the
  creator's voice (`narration: ""` / keep_audio scenes are wrong here — the audience doesn't understand it). The
  presenter narrates every scene over the clip's footage, explaining in the channel's language what the creator shows
  and teaches; he is on screen and speaking from start to end (user's rule, 2026-11-22). Exception: the creator's raw
  reaction at the climax (a scream, laugh, celebration — no sentence to understand), up to 4 s, may play with
  `visual.keep_audio: true` (e.g. IShowSpeed reacting while he plays).
