"""Smoke test of the editorial long video (cvm/render/editorial/assemble.py) end to end, with real Chromium and ffmpeg.

Everything is synthetic and lives in OUT (never the real data dir): a throwaway SQLite DB, a ~28 s narration read by
macOS `say` (a tone elsewhere), a test-pattern "footage" clip, a photo, a proof screenshot with one highlighted
phrase, and a plan.json with 3 cards + 1 proof + 1 photo + 1 video; no bumper (the channel has no logo), no music.
Writes OUT/sample.mp4 (~48 s: 28 s of scenes + the 20 s end screen) and frames at 2, 10, 20 s and in the end screen.

    cd backend && uv run python ../scripts/editorial_smoke.py [OUT]
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/editorial-smoke").resolve()
NARRATION_S = 28.0


def ff(*args):
    from cvm.config import settings

    subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", *map(str, args)], check=True)


def fixtures(d: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    d.mkdir(parents=True, exist_ok=True)
    clip, photo, proof, bg, narr = (d / n for n in ("clip.mp4", "foto.jpg", "prova.png", "bg.jpg", "narracao.wav"))
    ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8", "-c:v", "libx264", "-pix_fmt", "yuv420p", clip)
    ff("-f", "lavfi", "-i", "mandelbrot=size=1600x1000:rate=1", "-frames:v", "1", photo)
    ff("-i", photo, "-vf", "scale=1920:1080,gblur=sigma=28,eq=brightness=-0.22:saturation=0.8", "-frames:v", "1", bg)
    # a "news page" with the phrase to highlight
    img = Image.new("RGB", (1200, 600), "white")
    dr = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    lines = ["Notícias de teste · 24/11/2026", "", "O projeto publicou o código ontem.",
             "Segundo a equipe, o carro custa caro demais", "para chegar às lojas este ano."]
    for i, t in enumerate(lines):
        dr.text((60, 60 + i * 60), t, fill="black", font=font)
    img.save(proof)
    x0, y0, x1, y1 = dr.textbbox((60, 60 + 3 * 60), "Segundo a equipe, o carro custa caro demais", font=font)
    boxes = [{"rects": [[x0, y0, x1 - x0, y1 - y0]]}]
    text = ("Olha só essa história. Um carro que voa e custa caro. Vamos ver o que já se sabe, o que a fonte diz "
            "e por que isso importa para você. No fim, a minha opinião sincera sobre o projeto.")
    aiff = d / "narr.aiff"
    if shutil.which("say"):
        subprocess.run(["say", "-v", "Luciana", "-o", str(aiff), text], check=False)
    src = ["-i", aiff] if aiff.exists() else ["-f", "lavfi", "-i", "sine=f=220:d=20"]
    ff(*src, "-af", f"aresample=48000,apad=whole_dur={NARRATION_S},atrim=0:{NARRATION_S}", "-ac", "1", narr)
    return {"clip": clip, "photo": photo, "proof": proof, "bg": bg, "narr": narr, "boxes": boxes}


def plan(fx: dict) -> list[dict]:
    bg = str(fx["bg"])
    sec1, sec2 = ["01", "O CARRO QUE *VOA*"], ["02", "QUANTO *CUSTA*"]
    return [
        {"em": "Olha", "tipo": "pergunta", "texto": "ISSO É *REAL*?", "sub": "um carro que voa", "secao": sec1,
         "secao_nova": True, "som": "riser", "_w": 0, "start": 0.0, "end": 4.0, "bg": bg},
        {"em": "Um", "tipo": "video", "mov": "in", "secao": sec1, "_w": 3, "start": 4.0, "end": 9.0, "bg": bg,
         "fonte": "Wikimedia Commons (teste)",
         "media": {"kind": "video", "file": str(fx["clip"]), "start": 1.0, "window_end": 6.5,
                   "credit": "Wikimedia Commons (teste)", "source": "https://commons.wikimedia.org", "own": False}},
        {"em": "Vamos", "tipo": "numero", "valor": "US$ 300 mil", "rotulo": "o preço anunciado", "selo": "OFICIAL",
         "secao": sec2, "secao_nova": True, "varre": True, "som": "pop", "fonte": "Notícias de teste", "_w": 12,
         "start": 9.0, "end": 13.0, "bg": bg},
        {"em": "fonte", "tipo": "foto", "mov": "pan-d", "secao": sec2, "_w": 18, "start": 13.0, "end": 18.0, "bg": bg,
         "media": {"kind": "photo", "file": str(fx["photo"]), "start": 0.0, "window_end": None,
                   "credit": "Openverse (teste)", "own": False}},
        {"em": "diz", "tipo": "prova", "secao": sec2, "_w": 20, "start": 18.0, "end": 23.0, "bg": bg, "flash": True,
         "fonte": "noticias.example.com", "img": str(fx["proof"]), "size": [1200, 600], "boxes": fx["boxes"],
         "_hl": [1.0]},
        {"em": "No", "tipo": "lista", "estilo": "fluxo", "secao": sec2, "_w": 30, "start": 23.0, "end": 29.3,
         "bg": bg, "som": "pop", "itens": [["fim", "O QUE SE SABE"], ["opinião", "A FONTE"], ["projeto", "MINHA OPINIÃO"]],
         "_items": [{"t": 0.12, "text": "O QUE SE SABE"}, {"t": 1.6, "text": "A FONTE"},
                    {"t": 3.2, "text": "MINHA OPINIÃO"}]},
    ]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    from cvm.config import settings

    settings.data_dir = OUT / "data"  # never the real data dir
    import cvm.db
    from sqlmodel import Session, SQLModel, create_engine

    from cvm.models import Asset, Channel, Project, Script
    from cvm.paths import project_dir
    from cvm.render.editorial import assemble

    eng = create_engine(f"sqlite:///{OUT / 'smoke.db'}")
    cvm.db.engine = eng
    SQLModel.metadata.drop_all(eng)
    SQLModel.metadata.create_all(eng)
    fx = fixtures(OUT / "fixtures")
    with Session(eng) as s:
        s.add(Channel(id=1, name="Canal de Teste", platform="youtube", language="pt-BR", presenter="editorial"))
        s.add(Project(id=1, title="Smoke", topic="carro voador", channel_id=1, languages=["pt-BR"],
                      platforms=["youtube"], style="explainer", music_source="none"))
        s.add(Script(project_id=1, language="pt-BR", version=1, content={"hook": "h", "scenes": []}))
        s.add(Asset(project_id=1, kind="narration", language="pt-BR", path=str(fx["narr"]),
                    meta={"duration": NARRATION_S, "scenes": []}))
        s.commit()
        d = project_dir(1, "pt-BR") / "editorial"
        d.mkdir(parents=True, exist_ok=True)
        (d / "plan.json").write_text(json.dumps(plan(fx), ensure_ascii=False, indent=1))
        res = assemble.render(s, s.get(Project, 1), "pt-BR", OUT / "sample.mp4")
    print(json.dumps({"path": str(res.path), "duration": res.duration, "end_from": res.end_screen_from,
                      "chapters": res.chapters, "credits": res.credits}, ensure_ascii=False, indent=1))
    for t in (2, 10, 20, round(res.end_screen_from + 6, 1)):
        ff("-ss", t, "-i", res.path, "-frames:v", "1", OUT / f"frame_{t}s.png")
        print("frame", OUT / f"frame_{t}s.png")


if __name__ == "__main__":
    main()
