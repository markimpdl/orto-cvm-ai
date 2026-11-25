"""Visual judge: Gemma 3 12B (vision) looks at generated images / clip frame strips and flags "AI slop".

Usage: judge.py --model <hf repo or path> --in items.json --out verdicts.json
items: [{"image": path, "context": "what the scene narrates", "kind": "image" | "clip",
         "rule"?: "kids_thumbnail" | "gaze" | "editorial_window"}]
verdicts: [{"ok": bool, "score": 1-10, "problems": [str], "raw": str}] in the same order.
The model is loaded once per call; run it under the heavy-model lock.
"""

import argparse
import json
import re
import sys

PROMPT = """You check AI-generated visuals for a YouTube channel before they go into a video.
{what}
The scene narration is: "{context}"

Step 1: describe in one sentence what you actually see, and say if it is a photo-like image or an illustration/cartoon.
Step 2: for each person or character, list every hand you can see, one by one, with what it is doing
("left hand on the head", "right hand holding a phone"). Count carefully: a hand resting on the face or head counts too.
Step 3: list ONLY defects you can point to in THIS picture, saying where they are. Do not list a defect just
because it is a common AI problem. If you see none, the list is empty. {style_rule}
Defects that count:
- a hand, finger, limb, face or eye that is deformed, extra, missing or merged
- big readable text, a logo or a sign that is garbled or misspelled (tiny unreadable print does not count)
- objects melting, fused together or physically impossible
- any flag or flag-like banner in an AI image (AI cannot draw national flags correctly): severity 3{clip_rule}
- the picture does not show what the narration is about{kids_rule}
Severity: 3 = a viewer notices it at a glance and it looks broken; 2 = noticeable when looking; 1 = minor.

Answer ONLY with JSON:
{{"description": "...", "style": "photo" | "illustration", "people": [{{"who": "...", "hands": ["...", "..."]}}], "defects": [{{"what": "...", "where": "...", "severity": 1-3}}], "matches_narration": true | false}}"""

STYLE_RULE = {
    "photo": "An illustration or cartoon style is a valid choice: never count the style itself as a defect.",
    "cartoon": "This is a children's cartoon: simplified, cute, glossy drawing is expected, never count the style itself as a defect.",
}

WHAT = {
    "image": "Look at this image.",
    "clip": "These are three frames (left to right: start, middle, end) of ONE short video clip, side by side.",
}

KIDS_RULE = ("\n- THIS IMAGE IS THE THUMBNAIL OF A CHILDREN'S VIDEO (ages 3-8), so also count, severity 3: a distorted or "
             "deformed face; a mouth opened wide or exaggerated (screaming, shouting, a huge open-mouth grin); a scary, angry, "
             "crying or shocked expression; anything frightening (monsters, darkness, danger, sharp teeth). The characters "
             "must look calm, friendly and happy.")

CLIP_RULE = ("\n- between the frames: a hand, person or object that appears from nowhere, multiplies, vanishes or "
             "changes shape (severity 3); the frames being similar is normal")


WINDOW_PROMPT = """These are three frames (left to right: start, middle, end) of ONE short window of a REAL video clip,
side by side. It will be shown in a news video about: "{context}"
Answer ONLY with JSON: {{"watermark": true | false, "hard_cut": true | false, "stray_text": true | false, "what": "..."}}
- watermark: a TV channel bug, a logo or watermark of another creator, broadcaster or stock site in a corner or over
  the picture (the subject's own name or logo, e.g. a game's title in its own trailer, does not count)
- hard_cut: the three frames are from different shots (a scene cut or a transition happens inside the window)
- stray_text: big captions, subtitles or on-screen text that are not the subject itself
- what: one sentence on what you see"""

GAZE_PROMPT = ("Where are this man's eyes looking? Answer only JSON: "
               '{"looking": "at the camera" | "to the side" | "up" | "down" | "eyes closed"}')


def extra_hands(v: dict) -> list[dict]:
    """A person can't have more than two hands. The model lists each person's hands one by one with what they do
    (asked only "deformed?", it let a boy with two hands on his head and a third holding a phone through with 10/10).
    Fingers are not counted: Gemma 3 recites "thumb, index, middle, ring, pinky" even on a six-finger hand, and
    asking for fingers in the same answer broke the hand count (tested 2026-11-15)."""
    out = []
    for p in v.get("people") or []:
        hands = (p or {}).get("hands") if isinstance(p, dict) else None
        if isinstance(hands, list) and len(hands) > 2:
            who = str(p.get("who") or "person")[:40]
            out.append({"what": f"{len(hands)} hands on one person ({who}): " + "; ".join(str(h)[:40] for h in hands[:4]),
                        "where": "the person", "severity": 3})
    return out


def score_of(v: dict) -> float | None:
    """10 minus the squared severities: one severity-3 defect fails (1), one severity-2 sits on the bar (6)."""
    d = v.get("defects")
    if not isinstance(d, list):
        return None
    pen = 0
    for x in d:
        try:
            pen += min(3, max(1, int((x or {}).get("severity", 1)))) ** 2
        except (TypeError, ValueError, AttributeError):
            pen += 1
    if v.get("matches_narration") is False:
        pen += 4
    return float(max(1, 10 - pen))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=500)
    a = ap.parse_args()
    items = json.load(open(a.inp))

    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    model, processor = load(a.model)
    config = load_config(a.model)
    out = []
    for it in items:
        if it.get("rule") == "gaze":  # a presenter frame cropped to the face (cvm/avatar/talking.py gaze check)
            chat = apply_chat_template(processor, config, GAZE_PROMPT, num_images=1)
            try:
                res = generate(model, processor, chat, image=[it["image"]], max_tokens=40, temperature=0.0, verbose=False)
                text = getattr(res, "text", res)
                m = re.search(r'"looking"\s*:\s*"([^"]+)"', text)
                out.append({"looking": m.group(1) if m else None, "raw": text[:200]})
            except Exception as e:  # noqa: BLE001
                out.append({"looking": None, "raw": f"error: {e}"})
            continue
        if it.get("rule") == "editorial_window":  # a window of a real clip for the editorial format (cvm/visuals/editorial.py)
            chat = apply_chat_template(processor, config, WINDOW_PROMPT.format(context=(it.get("context") or "")[:400]),
                                       num_images=1)
            try:
                res = generate(model, processor, chat, image=[it["image"]], max_tokens=160, temperature=0.0, verbose=False)
                text = getattr(res, "text", res)
                m = re.search(r"\{.*\}", text, re.S)
                v = json.loads(m.group(0)) if m else {}
            except Exception as e:  # noqa: BLE001 — no answer: the caller treats it as no judge, never as clean
                out.append({"clean": None, "problems": ["juiz indisponível"], "raw": f"error: {e}"})
                continue
            problems = [name for name, key in (("logo/marca d'água de terceiro", "watermark"), ("corte de cena", "hard_cut"),
                                               ("texto na tela", "stray_text")) if v.get(key) is True]
            out.append({"clean": not problems, "problems": problems, "description": v.get("what"), "raw": text[:300]})
            continue
        kind = it.get("kind", "image")
        prompt = PROMPT.format(style_rule=STYLE_RULE[it.get("style", "photo")], what=WHAT[kind],
                               context=(it.get("context") or "")[:400], clip_rule=CLIP_RULE if kind == "clip" else "",
                               kids_rule=KIDS_RULE if it.get("rule") == "kids_thumbnail" else "")
        chat = apply_chat_template(processor, config, prompt, num_images=1)
        try:
            res = generate(model, processor, chat, image=[it["image"]], max_tokens=a.max_tokens, temperature=0.0,
                           verbose=False)
            text = getattr(res, "text", res)
        except Exception as e:  # one bad file must not sink the batch
            out.append({"ok": True, "score": None, "problems": [], "raw": f"error: {e}"})
            continue
        m = re.search(r"\{.*\}", text, re.S)
        try:
            v = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            v = {}
        if isinstance(v.get("defects"), list):
            v["defects"] += extra_hands(v)
        score = score_of(v)
        defects = [x for x in v.get("defects") or [] if isinstance(x, dict)]
        problems = [f"{x.get('what', '')} ({x.get('where', '')}) [{x.get('severity', 1)}]" for x in defects][:6]
        if v.get("matches_narration") is False:
            problems.append("não mostra o que a narração diz")
        out.append({"score": score, "problems": problems, "description": v.get("description"),
                    "matches_narration": v.get("matches_narration"), "raw": text[:600]})
        print(json.dumps({"i": len(out) - 1, "score": score}), file=sys.stderr, flush=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
