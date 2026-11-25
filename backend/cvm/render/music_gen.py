"""Own background music, generated locally by ACE-Step 1.5 (models/ACE-Step-1.5, MIT code and weights; the generated
music may be used commercially). Instrumental beds per style, kept as a small library in data/music/acestep/<style>/
so most videos reuse a track and generation only runs when a style has fewer than LIBRARY_TARGET tracks.
The model runs from its own venv (cvm_music.py there), one load per batch, under the heavy-model lock.
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from cvm.config import settings

log = logging.getLogger(__name__)
ACE = settings.models_dir / "ACE-Step-1.5"
LIBRARY_TARGET = 4  # tracks per style
TRACK_S = 150  # long enough to cover most videos; longer ones loop with a crossfade in the mix
CAPTIONS = {  # instrumental beds that sit under a voice: no lead melody fighting the narration
    "dark": "dark ambient cinematic underscore, slow pulsing synth pads, low drones, distant percussion, mysterious and tense",
    "explainer": "light modern background music for a tech explainer, soft electronic beat, warm keys, curious and positive",
    "top_list": "energetic countdown background music, punchy drums, bright synth plucks, building momentum",
    "react": "upbeat playful background groove for a commentary video, funky bass, light drums",
    "kids": "cheerful children's cartoon music, xylophone, ukulele, gentle claps, happy and simple melody",
    "lesson": "calm lo-fi study background music, soft piano, mellow beat, focused and warm",
    "review": "upbeat pop electronic background music for a product review, modern and confident",
}


def ready() -> bool:
    ck = ACE / "checkpoints"
    return (ACE / ".venv" / "bin" / "python").exists() and (ACE / "cvm_music.py").exists() and \
        (ck / "acestep-v15-turbo").exists() and any(ck.glob("acestep-5Hz-lm-*"))


def library(style: str) -> Path:
    return settings.music_dir / "acestep" / (style if style in CAPTIONS else "explainer")


def grow(style: str, seed: int) -> list[Path]:
    """Fill the style's library up to LIBRARY_TARGET with new tracks (one model load); returns the new files."""
    from cvm import gpu

    folder = library(style)
    folder.mkdir(parents=True, exist_ok=True)
    have = len(list(folder.glob("*.wav")))
    if have >= LIBRARY_TARGET or not ready():
        return []
    key = folder.name
    jobs = [{"caption": CAPTIONS[key] + ", instrumental, no vocals", "seconds": TRACK_S, "seed": seed + i,
             "out": str(folder / f"{key}_{seed + i}.wav")} for i in range(LIBRARY_TARGET - have)]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(jobs, f)
    with gpu.section():
        proc = subprocess.run([str(ACE / ".venv" / "bin" / "python"), "cvm_music.py", f.name], cwd=ACE,
                              capture_output=True, text=True, timeout=60 * 60)
    Path(f.name).unlink(missing_ok=True)
    made = []
    for line in proc.stdout.splitlines():
        if line.startswith("{") and (r := json.loads(line)).get("ok"):
            p = Path(r["out"])
            p.with_suffix(".json").write_text(json.dumps({"source": "acestep", "caption": CAPTIONS[key],
                                                          "license": "own"}, ensure_ascii=False))
            made.append(p)
    if not made:
        log.warning("acestep music: nothing generated: %s", (proc.stderr or proc.stdout)[-800:])
    return made
