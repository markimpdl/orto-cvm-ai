"""The editorial (news-desk) long video, assembled from `<project>/<lang>/editorial/plan.json` (cvm/visuals/editorial.py).

Ported from the bake-off renderer (bakeoff/editorial/render.py `main`, `make_sfx`, `mix_audio`, `sweeps_and_flash`):
- every scene becomes one 1920×1080 30 fps segment (cards.py), cached by a hash of what it shows
  (`seg/segNNN_<sig>.mp4`), so a re-render only redraws the scenes that changed;
- the "vinheta" scene becomes the channel's short bumper (`bumper.short_bumper`); when the bumper is longer than the
  pause the narration left for it, the rest of the video (and of the narration) moves later by the difference;
- the last 20 s are YouTube's end screen: slow, dimmed windows of footage the video already used (else its section
  backgrounds) with the channel's logo and no text, music only;
- audio: narration + synthesised effects (no rights involved) + the music bed (−16 LUFS, 12 dB under, ducked by the
  voice), loudness −14 LUFS; no music → narration and effects only;
- a slanted accent panel sweeps over each `varre` cut and a white flash marks each `flash` scene.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import Session

from cvm import progress
from cvm.media import duration, probe, run_ffmpeg
from cvm.models import Channel, Project
from cvm.paths import project_dir
from cvm.render.editorial import cards
from cvm.render.editorial.timeline import EditorialError
from cvm.render.music import credit_for, pick_music

log = logging.getLogger(__name__)

FPS = cards.FPS
W, H = cards.W, cards.H
END_S = 20.0  # YouTube's end screen: the last 20 s carry no text
END_PIECE_S = 5.0  # end screen pieces are about this long (fewer when the footage allows)
END_MAX_PIECES = 8
SEG_VERSION = 1  # bump to redraw every cached segment
ACCENT = "#FFD21E"  # the page's default accent (cards.html)
FINAL = ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-movflags", "+faststart"]

# sound effects (synthesised: no rights involved)
SFX = {
    "whoosh": "anoisesrc=color=pink:d=0.55:a=0.6,highpass=f=500,lowpass=f=5200,afade=t=in:d=0.28:curve=exp,"
              "afade=t=out:st=0.28:d=0.27,volume=0.55",
    "pop": "aevalsrc='0.8*sin(2*PI*(520+900*exp(-t*38))*t)*exp(-t*26)':d=0.18,highpass=f=180",
    "impacto": "aevalsrc='0.95*sin(2*PI*(46+90*exp(-t*14))*t)*exp(-t*4.2)+0.25*(random(0)*2-1)*exp(-t*38)':d=1.1,"
               "lowpass=f=2400",
    "riser": "aevalsrc='0.35*(random(0)*2-1)*pow(t/1.6,2)+0.25*sin(2*PI*(180+500*t*t)*t)*t/1.6':d=1.6,highpass=f=250,"
             "afade=t=out:st=1.5:d=0.1",
    "marca": "anoisesrc=color=white:d=0.32:a=0.35,bandpass=f=3400:width_type=h:w=2600,afade=t=in:d=0.04,"
             "afade=t=out:st=0.2:d=0.12,volume=0.5",
}
VOL = {"whoosh": .55, "pop": .35, "impacto": .7, "riser": .35, "marca": .3, "bumper": 1.0}


@dataclass
class Assembled:
    path: Path
    duration: float
    end_screen_from: float
    chapters: list[dict] = field(default_factory=list)
    music: Path | None = None
    credits: list[str] = field(default_factory=list)
    first_segment: Path | None = None
    bumper: dict | None = None  # {"at": s, "seconds": s the content after `at` moved}


def _q(t: float) -> float:
    """A time on the 30 fps frame grid (segments are cut to whole frames, so the joined video never drifts)."""
    return round(round(t * FPS) / FPS, 4)


# ---------- timeline ----------

def layout(plan: list[dict], narration_s: float, bumper_s: float = 0.0) -> tuple[list[dict], float, dict | None]:
    """The plan's scenes on the final timeline + the end screen. Returns (scenes, end screen start, bumper meta)."""
    scenes = [dict(s) for s in plan]
    shift, bumper = 0.0, None
    for k, s in enumerate(scenes):
        if s["tipo"] == "vinheta" and bumper_s > 0:
            slot = s["end"] - s["start"]
            shift = _q(max(0.0, bumper_s - slot))
            s["end"] = s["start"] + max(bumper_s, slot)
            s["bumper"] = True
            for later in scenes[k + 1:]:
                later["start"] += shift
                later["end"] += shift
            bumper = {"at": round(s["start"], 2), "seconds": round(shift, 2)}
            break
    end_from = _q(narration_s + shift)
    content = [s for s in scenes if s["start"] < end_from - 0.2] or scenes[:1]
    for k, s in enumerate(content):  # frame-aligned and back to back
        s["start"] = 0.0 if k == 0 else _q(s["start"])
    for a, b in zip(content, content[1:]):
        a["end"] = b["start"]
    content[-1]["end"] = end_from
    return content + end_scenes(content, end_from), end_from, bumper


def _end_pool(scenes: list[dict]) -> tuple[list[tuple[dict, float]], list[dict]]:
    """(video windows with the longest piece each can cover, stills: photos then section backgrounds)."""
    vids, stills, seen = [], [], set()
    for s in scenes:
        m = s.get("media") or {}
        if not m.get("file") or (m["file"], m.get("start")) in seen:
            continue
        seen.add((m["file"], m.get("start")))
        if m.get("kind") == "video":
            if m.get("window_end") is not None and float(m["window_end"]) > float(m.get("start") or 0):
                vids.append((m, (float(m["window_end"]) - float(m.get("start") or 0)) * cards.MAX_SLOW * 0.99))
        else:
            stills.append(m)
    for bg in dict.fromkeys(s.get("bg") for s in scenes if s.get("bg")):
        if Path(bg).exists():
            stills.append({"kind": "photo", "file": bg, "start": 0.0, "window_end": None, "own": True})
    return vids, stills


def _spread(items: list, n: int) -> list:
    if len(items) <= n:
        return list(items)
    return [items[round(i * (len(items) - 1) / (n - 1))] for i in range(n)] if n > 1 else [items[-1]]


def end_scenes(scenes: list[dict], t0: float, total: float = END_S) -> list[dict]:
    """The end screen: slow, dimmed windows of footage the video already showed (stills when there is not enough),
    logo only, no text; one plain card when the video had no picture at all."""
    vids, stills = _end_pool(scenes)
    first = max(1, round(total / END_PIECE_S))
    for n in range(first, END_MAX_PIECES + 1):  # fewer, longer pieces first
        step = total / n
        ok = [m for m, cap in vids if cap >= step]
        picked = _spread(ok, n) + stills[:max(0, n - len(ok))]
        if picked:
            break
    else:
        bg = next((s.get("bg") for s in scenes if s.get("bg")), None)
        return [{"tipo": "fim", "start": t0, "end": _q(t0 + total), "bg": bg, "end_screen": True, "secao": None}]
    out = []
    for i in range(n):
        m = picked[i % len(picked)]
        a, b = _q(t0 + i * total / n), _q(t0 + (i + 1) * total / n)
        out.append({"tipo": "video" if m.get("kind") == "video" else "foto", "fim": True, "mov": "in",
                    "media": dict(m), "start": a, "end": b, "secao": None, "end_screen": True})
    return out


def chapters(scenes: list[dict], end_from: float, min_s: float = 10.0) -> list[dict]:
    """YouTube chapters from the sections: one per section opening, the first at 0:00, each ≥ 10 s, none inside the
    end screen, at least three (else none)."""
    out: list[dict] = []
    for s in scenes:
        if s.get("end_screen") or not (s.get("secao_nova") and s.get("secao")):
            continue
        sec = s["secao"]
        title = re.sub(r"\s+", " ", str(sec[1] if len(sec) > 1 else sec[0]).replace("*", "")).strip()
        if not title:
            continue
        if not out:
            out.append({"t": 0.0, "title": title})
        elif s["start"] - out[-1]["t"] >= min_s:
            out.append({"t": round(s["start"], 2), "title": title})
    if len(out) > 1 and end_from - out[-1]["t"] < min_s:
        out.pop()
    return out if len(out) >= 3 else []


# ---------- audio ----------

def events(scenes: list[dict]) -> list[tuple[str, float]]:
    """(effect, second) for the mix: each scene's `som`, a whoosh under every sweep, a mark per highlighted phrase,
    a pop per list item after the first; the bumper's own sound (or whoosh + hit for a page-drawn vinheta)."""
    ev: list[tuple[str, float]] = []
    for s in scenes:
        if s.get("end_screen"):
            continue
        st = s["start"]
        if s.get("som"):
            ev.append((s["som"], st + (s.get("impacto_t") or 0 if s["som"] == "impacto" else 0)))
        if s.get("varre"):
            ev.append(("whoosh", max(0.0, st - .22)))
        for t in s.get("_hl") or []:
            ev.append(("marca", st + t))
        if s.get("tipo") == "lista":
            ev += [("pop", st + it["t"]) for it in (s.get("_items") or [])[1:]]
        if s["tipo"] == "vinheta":
            ev += [("bumper", st)] if s.get("bumper") else [("whoosh", st), ("impacto", st + .18)]
    return ev


def make_sfx(d: Path) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    for k, expr in SFX.items():
        p = d / f"{k}.wav"
        if not p.exists():  # written aside and moved in: an interrupted run never leaves a half file behind
            part = p.with_name(p.name + ".part")
            run_ffmpeg("-f", "lavfi", "-i", expr, "-ar", "48000", "-ac", "2", "-f", "wav", str(part))
            os.replace(part, p)
    return {k: d / f"{k}.wav" for k in SFX}


def mix_audio(narration: Path, scenes: list[dict], total_v: float, out: Path, music: Path | None, sfx_dir: Path,
              bumper_audio: Path | None = None, insert: tuple[float, float] | None = None,
              end_from: float | None = None, music_ss: float = 0.0) -> Path:
    """Narration (with `insert` = (at, seconds) of silence for the bumper) + effects + the ducked music bed,
    loudness −14 LUFS. Inputs: 0 narration, then the music, then one input per effect used (split per event)."""
    ev = []
    for n, t in events(scenes):  # a silent bumper clip gets the page vinheta's whoosh + hit
        ev += [(n, t)] if n != "bumper" or bumper_audio else [("whoosh", t), ("impacto", t + .18)]
    sfx = make_sfx(sfx_dir) if any(n != "bumper" for n, _ in ev) else {}
    st = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    ins: list[str] = ["-i", str(narration)]
    fl: list[str] = []
    side = "asplit=2[nar][sc]" if music else "anull[nar]"
    if insert and insert[1] > 0:
        at, secs = insert
        ms = int(round(secs * 1000))
        fl += [f"[0:a]{st},asplit=2[na][nb]", f"[na]atrim=0:{at:.3f},asetpts=PTS-STARTPTS[na1]",
               f"[nb]atrim=start={at:.3f},asetpts=PTS-STARTPTS,adelay={ms}:all=1[nb1]",
               f"[na1][nb1]concat=n=2:v=0:a=1,apad=whole_dur={total_v:.3f},{side}"]
    else:
        fl.append(f"[0:a]{st},apad=whole_dur={total_v:.3f},{side}")
    mix = ["[nar]"]
    nxt = 1
    if music:
        ins += ["-stream_loop", "-1", "-ss", f"{music_ss:.2f}", "-i", str(music)]
        vin = next((s for s in scenes if s["tipo"] == "vinheta"), None)
        vs, ve = (vin["start"], vin["end"]) if vin else (-10.0, -9.0)
        boost = 0.5 if vin and vin.get("bumper") else 2.0  # the bumper has its own sound; a page vinheta has none
        ef = end_from if end_from is not None else total_v
        fl += [f"[1:a]{st},loudnorm=I=-16:TP=-2,aresample=48000,volume=-12dB,"
               f"volume='if(between(t,{vs:.2f},{ve:.2f}),{boost},1)*if(gte(t,{ef:.2f}),1.6,1)':eval=frame,"
               f"atrim=0:{total_v:.3f},afade=t=in:d=1.0,afade=t=out:st={max(0.0, total_v - 2.2):.3f}:d=2.2[bed]",
               "[bed][sc]sidechaincompress=threshold=0.05:ratio=3:attack=25:release=450:makeup=1[duck]"]
        mix.append("[duck]")
        nxt = 2
    kinds = list(dict.fromkeys(n for n, _ in ev))
    for name in kinds:
        times = [t for n, t in ev if n == name]
        ins += ["-i", str(bumper_audio if name == "bumper" else sfx[name])]
        labels = [f"[{name}{j}]" for j in range(len(times))]
        fl.append(f"[{nxt}:a]{st},volume={VOL[name]},asplit={len(times)}" + "".join(labels)
                  if len(times) > 1 else f"[{nxt}:a]{st},volume={VOL[name]},anull{labels[0]}")
        for j, t in enumerate(times):
            ms = int(max(0.0, t) * 1000)
            fl.append(f"{labels[j]}adelay={ms}:all=1[e_{name}{j}]")
            mix.append(f"[e_{name}{j}]")
        nxt += 1
    fl.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=longest,atrim=0:{total_v:.3f},"
              "loudnorm=I=-14:TP=-1.5:LRA=9,aresample=48000[a]")
    run_ffmpeg(*ins, "-filter_complex", ";".join(fl), "-map", "[a]", "-c:a", "pcm_s16le", str(out))
    return out


# ---------- video ----------

def sweeps_and_flash(scenes: list[dict], video: Path, audio: Path, out: Path, sweep_png: Path, total: float) -> Path:
    """The accent panel crossing each `varre` cut (it covers the cut), a white flash where asked; muxes the audio."""
    fl = ["[0:v]null[v0]"]
    last, k = "v0", 0
    sweeps = [s for s in scenes if s.get("varre") and not s.get("end_screen")]
    ins = ["-i", str(video), "-i", str(audio)]
    if sweeps:
        ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{total:.3f}", "-i", str(sweep_png)]
    for s in scenes:
        if s.get("end_screen"):
            continue
        T = s["start"]
        if s.get("varre"):
            u = f"((t-{T:.3f})/0.28)"  # u: -1..1 over 0.56 s around the cut
            x = f"-3400+(5320)*(0.5+0.2*{u}+0.3*{u}*{u}*{u})"
            fl.append(f"[{last}][2:v]overlay=x='{x}':y=0:enable='between(t,{T - .28:.3f},{T + .28:.3f})'[w{k}]")
            last, k = f"w{k}", k + 1
        if s.get("flash"):
            fl.append(f"[{last}]eq=brightness='if(between(t,{T:.3f},{T + .12:.3f}),0.45*(1-(t-{T:.3f})/0.12),0)'"
                      f":eval=frame[w{k}]")
            last, k = f"w{k}", k + 1
    run_ffmpeg(*ins, "-filter_complex", ";".join(fl), "-map", f"[{last}]", "-map", "1:a", *FINAL,
               "-t", f"{total:.3f}", str(out))
    return out


def _sweep_png(colour: str, dest: Path) -> Path:
    """The sweep panel (accent slab + dark edge, slanted), 3400×1080 with transparency; made once per colour."""
    out = dest / f"sweep_{hashlib.sha1(colour.encode()).hexdigest()[:8]}.png"
    if out.exists():
        return out
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            page = b.new_page(viewport={"width": 3400, "height": H})
            page.set_content(
                "<html><body style='margin:0;background:transparent'>"
                f"<div style='position:absolute;left:300px;top:-200px;width:2800px;height:1480px;background:{colour};"
                "transform:skewX(-18deg)'></div><div style='position:absolute;left:180px;top:-200px;width:70px;"
                f"height:1480px;background:{cards.INK};transform:skewX(-18deg)'></div></body></html>")
            part = out.with_name(out.name + ".part")  # moved in once complete (an interrupted run leaves no half PNG)
            page.screenshot(path=str(part), type="png", omit_background=True)
            os.replace(part, out)
        finally:
            b.close()
    return out


def _bumper_segment(clip: Path, dur: float, out: Path) -> None:
    run_ffmpeg("-i", str(clip), "-vf", f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=decrease,"
               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,setsar=1,tpad=stop_mode=clone:stop_duration={dur:.3f}",
               "-an", *cards.ENC, "-t", f"{dur:.4f}", str(out))


def _sig(scene: dict, spec: dict, dur: float, extra=None) -> str:
    m = scene.get("media") or {}
    files = [p for p in (m.get("file"), scene.get("img"), scene.get("bg")) if p]
    stamps = [int(Path(p).stat().st_mtime) if Path(p).exists() else 0 for p in files]
    key = [SEG_VERSION, spec, dur, m.get("file"), m.get("start"), m.get("window_end"), m.get("crop"), scene.get("mov"),
           stamps, cards.TEMPLATE.stat().st_mtime if cards.TEMPLATE.exists() else 0, extra]
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _segments(scenes: list[dict], brand: dict, lang: str, dest: Path, bumper_clip: Path | None) -> list[Path]:
    """One mp4 per scene (cached by what it shows); stale segments of earlier runs are removed."""
    seg = dest / "seg"
    seg.mkdir(parents=True, exist_ok=True)
    outs, todo = [], []
    for k, s in enumerate(scenes):
        dur = round(s["end"] - s["start"], 4)
        if s.get("bumper") and bumper_clip:
            spec = {"bumper": str(bumper_clip)}
            sig = _sig(s, spec, dur, int(bumper_clip.stat().st_mtime))
        else:
            spec = cards.page_spec(s, brand, lang)  # the VIDEO's language (footer labels)
            sig = _sig(s, spec, dur)
        out = seg / f"seg{k:03d}_{sig}.mp4"
        outs.append(out)
        if not out.exists():
            todo.append((k, s, spec, dur, out))
    keep = set(outs)
    for old in seg.glob("seg*.mp4"):
        if old not in keep:
            old.unlink(missing_ok=True)
    if not todo:
        return outs
    browser = page = None
    pw = None
    try:
        for i, (k, s, spec, dur, out) in enumerate(todo):
            progress.report(i, len(todo), f"cenas editoriais ({i}/{len(todo)})")
            part = out.with_name(out.stem + ".part.mp4")
            if "bumper" in spec:
                _bumper_segment(bumper_clip, dur, part)
                part.replace(out)
                continue
            if page is None:
                from playwright.sync_api import sync_playwright

                pw = sync_playwright().start()
                browser = pw.chromium.launch()
                page = cards.open_page(browser)
            fdir = dest / "frames" / f"{k:03d}"
            shutil.rmtree(fdir, ignore_errors=True)
            media = s.get("media") or {}
            over = s["tipo"] in ("video", "foto") and media.get("file")
            lst, _ = cards.render_frames(page, spec, dur, fdir, transparent=bool(over))
            if over and media.get("kind") == "video":
                cards.video_segment(s, dur, lst, part)
            elif over:
                cards.photo_segment(s, dur, lst, part)
            else:
                cards.card_segment(lst, dur, part)
            part.replace(out)
            shutil.rmtree(fdir, ignore_errors=True)
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()
    return outs


def _concat(segs: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".ffconcat")
    lst.write_text("ffconcat version 1.0\n" + "".join(f"file '{p.as_posix()}'\n" for p in segs))
    run_ffmpeg("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out))
    return out


def _has_audio(path: Path) -> bool:
    try:
        return any(st.get("codec_type") == "audio" for st in probe(path).get("streams", []))
    except Exception:  # noqa: BLE001
        return False


def _bumper_clip(session: Session, project: Project, channel: Channel | None, plan: list[dict]) -> Path | None:
    from cvm.render import bumper

    if not any(s["tipo"] == "vinheta" for s in plan) or not channel:
        return None
    if not bumper.wants_bumper(channel, "youtube", None, project):
        return None
    try:
        return bumper.short_bumper(channel, W, H, FPS)
    except Exception as e:  # noqa: BLE001 — the page draws the vinheta instead
        log.warning("project %s: short bumper unavailable, the page draws the vinheta: %s", project.id, e)
        return None


def render(session: Session, project: Project, language: str, out: Path) -> Assembled:
    """Assemble the editorial long video of `project` in `language` into `out` (an mp4)."""
    from cvm.voice.narrate import narration_asset

    narr = narration_asset(session, project.id, language)
    if not narr or not Path(narr.path).exists():
        err = EditorialError("Falta a narração deste idioma para montar o vídeo editorial.")
        err.fix_state = "voicing"
        raise err
    dest = project_dir(project.id, language) / "editorial"
    plan_path = dest / "plan.json"
    if not plan_path.exists():
        err = EditorialError("O plano de cenas editoriais (plan.json) não existe; refaça a busca de imagens.")
        err.fix_state = "sourcing_visuals"
        raise err
    plan = json.loads(plan_path.read_text())
    if not plan:
        err = EditorialError("O plano de cenas editoriais está vazio; refaça a busca de imagens.")
        err.fix_state = "sourcing_visuals"
        raise err
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    brand = cards.brand_for(channel)
    bumper_clip = _bumper_clip(session, project, channel, plan)
    narration_s = float((narr.meta or {}).get("duration") or duration(narr.path))
    scenes, end_from, bumper_meta = layout(plan, narration_s, duration(bumper_clip) if bumper_clip else 0.0)
    total_v = scenes[-1]["end"]

    segs = _segments(scenes, brand, language, dest, bumper_clip)
    joined = _concat(segs, dest / "joined.mp4")
    music = pick_music(project, (channel.prefs or {}) if channel else None, session)
    music_ss = 0.0
    if music:
        from cvm.render.music import energetic_start

        music_ss = energetic_start(music, total_v)
    audio = dest / "audio.wav"
    vin = next((s for s in scenes if s.get("bumper")), None)
    mix_audio(Path(narr.path), scenes, total_v, audio, music, dest / "sfx",
              bumper_audio=bumper_clip if bumper_clip and _has_audio(bumper_clip) else None,
              insert=(vin["start"], bumper_meta["seconds"]) if vin and bumper_meta else None,
              end_from=end_from, music_ss=music_ss)
    colour = cards._readable_accent(brand.get("colours") or {}) or ACCENT
    sweep = _sweep_png(colour, dest) if any(s.get("varre") for s in scenes) else dest / "sweep.png"
    sweeps_and_flash(scenes, joined, audio, out, sweep, total_v)

    credits = sorted({c for s in scenes if not s.get("end_screen")
                      for c in [(s.get("media") or {}).get("credit")] if c and not (s.get("media") or {}).get("own")})
    if music and (c := credit_for(music)):
        credits.append(c)
    (dest / "render.json").write_text(json.dumps(scenes, ensure_ascii=False, indent=1, default=str))
    log.info("project %s %s: editorial video %.1f s (%d scenes, end screen from %.1f s)", project.id, language,
             total_v, len(scenes), end_from)
    return Assembled(path=Path(out), duration=round(total_v, 2), end_screen_from=round(end_from, 2),
                     chapters=chapters(scenes, end_from), music=music, credits=credits,
                     first_segment=segs[0] if segs else None, bumper=bumper_meta)
