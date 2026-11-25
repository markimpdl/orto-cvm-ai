---
name: script-style-explainer
description: Writing rules for explainer/news videos — the avatar presents a trending topic with evidence (headlines, charts, B-roll).
---
- Structure: hook (what happened + why it matters to the viewer) → 3 key points → what happens next → CTA.
- Visuals: `avatar_full` for hook/CTA, `headline` with the source article URL for evidence, `chart` when there are numbers: fill `visual.chart` with the data ({kind: bar|timeline|stat, title, unit, items: [{label, value | text, highlight?}], source}, labels in the script's language, 2–6 items); without `visual.chart` the scene is only a text card, `broll` otherwise.
- Neutral, clear, conversational; cite the outlet when quoting.
- Open with the misconception or the consequence for the viewer, then the fact that corrects it ("You think X. Actually Y.").
- One concept per ~30 s; after the key insight, a short beat with no new info (a visual or one-line recap) so it lands.
- Long videos: each key point is one 60–90 s `block` (its title = the chapter: the point in a few words), opened by the viewer's question and closed by the answer; "what happens next" is the last block.
