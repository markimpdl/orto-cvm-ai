"""Packs the owner's avatar "Lucas" into assets/avatars/lucas.zip (read-only on the source data).

    python scripts/pack_lucas.py [--data <data dir>]

Only pt-BR and en-US voices (reference file + text), base/, podcast/ (without takes), the photo PNGs and the
thumbnail cut-out; paths in avatar.json are relative. Fails above 90 MB.
"""
import argparse
import json
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "avatars" / "lucas.zip"
LANGS = ("pt-BR", "en-US")
MAX_MB = 90
SOURCE_NAME = "Lucas (IA)"
NOTES = "criado a partir de foto (animado por IA)"


def _files(src: Path, voices: list[dict]) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for sub in ("base", "podcast"):
        for p in sorted((src / sub).rglob("*")):
            if p.is_file() and "takes" not in p.relative_to(src / sub).parts:
                out.append((p, p.relative_to(src).as_posix()))
    for p in sorted((src / "photo").glob("*.png")):
        out.append((p, f"photo/{p.name}"))
    for v in voices:
        out.append((src / v["ref_file"], v["ref_file"]))
    cut = sorted(src.glob("thumb_cutout_*.png"), key=lambda p: p.stat().st_mtime)
    if cut:
        out.append((cut[-1], "thumb_cutout.png"))
    return out


def manifest(source_dir: Path, avatar_row: dict, voice_rows: list[dict]) -> dict:
    """avatar.json content: relative paths, only LANGS, name without the "(IA)" suffix."""
    voices = []
    for v in voice_rows:
        if v["language"] not in LANGS:
            continue
        ref = Path(v["ref_audio_path"]).name
        voices.append({"language": v["language"], "engine": "voxcpm2", "ref_file": f"voice/{ref}",
                       "ref_text": v.get("ref_text"), "params": v.get("params") or {}})
    return {"avatar": {"name": "Lucas", "status": avatar_row.get("status") or "ready",
                       "notes": NOTES}, "voices": voices}


def main() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=None)
    data = ap.parse_args().data
    if data is None:
        from cvm.config import settings
        data = settings.data_dir
    con = sqlite3.connect(f"file:{Path(data) / 'cvm.sqlite'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    av = con.execute("select * from avatar where name=?", (SOURCE_NAME,)).fetchone()
    if not av:
        sys.exit(f'avatar "{SOURCE_NAME}" not found')
    rows = []
    for r in con.execute("select * from voiceprofile where avatar_id=?", (av["id"],)):
        d = dict(r)
        d["params"] = json.loads(d["params"]) if isinstance(d.get("params"), str) else (d.get("params") or {})
        rows.append(d)
    src = Path(data) / "avatars" / str(av["id"])
    man = manifest(src, dict(av), rows)
    text = json.dumps(man, ensure_ascii=False, indent=2)
    if "/Users/" in text or re.search(r"[A-Za-z]:\\", text):
        sys.exit("absolute path in avatar.json")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("avatar.json", text)
        for p, arc in _files(src, man["voices"]):
            z.write(p, arc)
    mb = OUT.stat().st_size / 1e6
    print(f"{OUT} {mb:.1f} MB")
    if mb > MAX_MB:
        OUT.unlink()
        sys.exit(f"zip above {MAX_MB} MB")


if __name__ == "__main__":
    main()
