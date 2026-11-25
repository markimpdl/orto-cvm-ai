"""Channel bumper ("vinheta") between the promise and the content of long videos.

Since 2026-11-13 (plan, Phase 5) it is SHORT by default: ≤ 3 s, the channel's logo animated over its blurred banner
with a short sound, no spoken line (the spoken subscribe ask moved into the script, at ~40% and at the end). The
same for every video of the channel (no language), kids series included. Needs data/channels/<id>/brand/logo.png.
Channel.prefs:
- "bumper_mode": "full" brings back the old 8–12 s bumper (presenter's voice asking to subscribe and like,
  bumper_brand.render with banner.png + logo.png, or a name + buttons card); "short" (default) otherwise.
- "bumper_music": path to an opening tune (the Arabic kids series): it replaces the short sound, cut to ≤ 5 s.
The full bumper's text can be set in Channel.prefs["bumper"][<lang>] = {"say": "...", "subscribe": "...", "like": "..."}.

Cached in data/channels/<id>/vinheta/. Shorts and cuts get none (a bumper in a Short makes people swipe).
"""

import hashlib
import logging
from pathlib import Path

from sqlmodel import Session

from cvm.config import settings
from cvm.media import H264, duration, filter_path, run_ffmpeg
from cvm.models import Channel

log = logging.getLogger(__name__)
VERSION = 5  # 5: softer whoosh (it read as static); 2: no letter spacing in Arabic (libass drops shaping when Spacing ≠ 0), slower background
SHORT_VERSION = 2  # 2: softer whoosh (it read as static on a kids episode)
SHORT_S = 2.8  # on screen, crossfades included (≤ 3 s)
MUSIC_MAX_S = 5.0  # an opening tune may run a little longer (Arabic kids series)

DEFAULTS = {
    "pt-BR": {"say": "Se inscreve no canal e deixa o seu like!", "subscribe": "INSCREVA-SE", "like": "DEIXE SEU LIKE"},
    "en-US": {"say": "Subscribe to the channel and drop a like!", "subscribe": "SUBSCRIBE", "like": "LIKE THE VIDEO"},
    "ar-AE": {"say": "اشترك في القناة، ولا تنسى اللايك!", "subscribe": "اشترك", "like": "لايك للفيديو"},
}
VOICE_STYLE = {"exaggeration": 0.85, "cfg_weight": 0.3}  # lively tone (engine.tone_instruct)
# per-channel accent (ASS colours are &HBBGGRR), picked from the channel id so every channel keeps its own
ACCENTS = ["00C8FF", "FF3D7F", "7CFF4D", "FFB000", "B36BFF", "00E5B0", "FF6A00"]


def mode(channel: Channel | None) -> str:
    """"short" (default) | "full" | "none" (no bumper at all: lesson channels start straight on the content)."""
    m = (getattr(channel, "prefs", None) or {}).get("bumper_mode")
    return m if m in ("full", "none") else "short"


def opening_music(channel: Channel) -> Path | None:
    p = (channel.prefs or {}).get("bumper_music")
    return Path(p) if p and Path(p).exists() else None


def brand_id(channel: Channel) -> int:
    """Whose logo/banner the short bumper uses: prefs.bumper_from = another channel's id (borrow its bumper),
    else the channel's own art in data/channels/<id>/brand."""
    other = (getattr(channel, "prefs", None) or {}).get("bumper_from")
    return int(other) if other else channel.id


def bumper_on(project, channel: Channel | None) -> bool:
    """The video's choice (Project.bumper) or else the channel's (bumper_mode "none" = off)."""
    chosen = getattr(project, "bumper", None)
    return chosen if chosen is not None else mode(channel) != "none"


def hook_on(project, channel: Channel | None) -> bool:
    """Opening hook + promise: the video's choice (Project.hook) or else the channel's prefs.hook (default yes)."""
    chosen = getattr(project, "hook", None)
    return chosen if chosen is not None else bool((getattr(channel, "prefs", None) or {}).get("hook", True))


def wants_bumper(channel: Channel | None, profile_name: str, part, project=None) -> bool:
    """Only channels with their art in data/channels/<id>/brand get a bumper (the user's choice, 2026-11-04;
    kids channels included since 2026-11-05, also the user's choice): the logo for the short one, logo + banner
    for the full one."""
    from cvm.render.bumper_brand import brand_files, logo_file

    if not (channel and not part and profile_name == "youtube") or not bumper_on(project, channel):
        return False
    return bool(brand_files(channel.id) if mode(channel) == "full" else logo_file(brand_id(channel)))


def short_bumper(channel: Channel, W: int, H: int, fps: int) -> Path:
    from cvm.render import bumper_brand

    logo = bumper_brand.logo_file(brand_id(channel))
    banner = settings.data_dir / "channels" / str(brand_id(channel)) / "brand" / "banner.png"
    banner = banner if banner.exists() else None
    music = opening_music(channel)
    d = min(MUSIC_MAX_S, max(SHORT_S, duration(music))) if music else SHORT_S
    colours = (channel.prefs or {}).get("brand") or {}
    # params approved in the install wizard's preview (prefs.bumper_params); channels without them render as before
    approved = (channel.prefs or {}).get("bumper_params") or {}
    style = {k: approved[k] for k in ("variant", "sound") if approved.get(k)}
    if approved.get("colours"):
        colours = {**colours, **approved["colours"]}
    stamp = "|".join(f"{p}:{int(p.stat().st_mtime)}" for p in (logo, banner, music) if p)
    extra = f"|{sorted(style.items())}" if style else ""
    key = hashlib.sha1(f"{SHORT_VERSION}|{stamp}|{colours}|{d:.2f}{extra}".encode()).hexdigest()[:8]
    folder = settings.data_dir / "channels" / str(brand_id(channel)) / "vinheta"
    final = folder / f"short_{W}x{H}_{key}.mp4"
    if final.exists() and final.stat().st_size > 0:
        return final
    folder.mkdir(parents=True, exist_ok=True)
    out = final.with_name(final.stem + ".partial.mp4")
    bumper_brand.render_short(logo, banner, W, H, fps, out, d, colours, music, **style)
    log.info("short bumper ready for channel %s (%.1f s)", channel.id, d)
    return out.replace(final)


def bumper_point(scenes: list[dict], intro_end: int | None, end: float) -> float | None:
    """Where the bumper goes (seconds into the narration): after `intro_end`, else — scripts written before the
    rule, kids episodes without it — at the first scene boundary between 15 and 45 s."""
    starts = [(t["index"], t["start"]) for t in scenes]
    if intro_end is not None:
        after = [s for i, s in starts if i > intro_end]
    else:
        after = [s for _, s in starts if 15 <= s <= 45][:1]
    return after[0] if after and 3 < after[0] < end - 5 else None


class GenericBumper(RuntimeError):
    """A kids channel's bumper would use the generic line or voice: never (the user's rule, 2026-11-13). Set
    Channel.prefs.bumper[<lang>].say and prefs.bumper_speaker (a series character)."""


def texts(channel: Channel, language: str) -> dict:
    base = DEFAULTS.get(language, DEFAULTS["en-US"])
    own = ((channel.prefs or {}).get("bumper") or {}).get(language) or {}
    if getattr(channel, "made_for_kids", False) and not (own.get("say") and (channel.prefs or {}).get("bumper_speaker")):
        raise GenericBumper(f"canal {channel.id} ({language}): vinheta infantil sem fala própria (prefs.bumper) "
                            "ou sem personagem (prefs.bumper_speaker)")
    return {**base, **own}


def _bgr(hex_rgb: str) -> str:
    return hex_rgb[4:6] + hex_rgb[2:4] + hex_rgb[0:2]


def channel_name(channel: Channel) -> str:
    return channel.name.split("@")[0].split("—")[0].strip()


def _voice(session: Session, channel: Channel, language: str, say: str, folder: Path, style: dict) -> Path:
    """The presenter's cloned voice saying the line, cached apart from the visuals (redesigns don't re-voice)."""
    from cvm import gpu
    from cvm.voice.engine import synthesize
    from cvm.voice.narrate import voice_profile

    speaker = (channel.prefs or {}).get("bumper_speaker")  # {"channel": id, "name": "Cacau"}: a series character speaks
    who = speaker or channel.avatar_id
    voice = folder / f"voice_{language}_{hashlib.sha1(f'{style}|{say}|{who}'.encode()).hexdigest()[:8]}.wav"
    if not voice.exists():
        profile = voice_profile(session, channel.avatar_id, language, (channel.prefs or {}).get("voice_variant"))
        if speaker:
            from cvm.voice import characters

            src = session.get(Channel, speaker["channel"])
            entry = characters.find(src, speaker["name"]) if src else None
            if entry:
                profile = characters._profile(src, speaker["name"], entry, language)
            elif channel.made_for_kids:  # never the avatar's/default voice on a kids channel
                raise GenericBumper(f"canal {channel.id}: personagem da vinheta {speaker} não encontrado")
        with gpu.section():
            synthesize(say, language, profile, voice, seed=3,
                       instruct="very energetic, excited, upbeat, smiling, fast", tone=style)
    return voice


def bumper_for(session: Session, channel: Channel, language: str, W: int, H: int, fps: int) -> Path:
    if mode(channel) != "full":
        return short_bumper(channel, W, H, fps)
    from cvm.render import bumper_brand

    t = texts(channel, language)
    name = channel_name(channel)
    brand = bumper_brand.brand_files(channel.id)
    stamp = "|".join(str(int(p.stat().st_mtime)) for p in brand) if brand else ""
    prefs = channel.prefs or {}
    style = prefs.get("bumper_voice") or VOICE_STYLE  # tone dict; a calmer channel sets softer ones
    colours = prefs.get("brand") or {}
    key = hashlib.sha1(f"{VERSION}|{name}|{t}|{channel.avatar_id}|{stamp}|{style}|{colours}|{prefs.get('bumper_speaker')}".encode()).hexdigest()[:8]
    folder = settings.data_dir / "channels" / str(channel.id) / "vinheta"
    out = folder / f"{language}_{W}x{H}_{key}.mp4"
    if out.exists() and out.stat().st_size > 0:
        return out
    final, out = out, out.with_name(out.stem + ".partial.mp4")  # renamed only when complete (a failed run never caches)
    work = final.with_suffix("")
    work.mkdir(parents=True, exist_ok=True)
    voice = _voice(session, channel, language, t["say"], folder, style)
    if brand:  # the channel's own banner + logo: the full motion-graphics version
        bumper_brand.render(*brand, voice, t, language, W, H, fps, work, out, colours)
        log.info("branded bumper ready for channel %s %s", channel.id, language)
        return out.replace(final)
    d = max(3.8, duration(voice) + 1.1)

    accent = ACCENTS[(channel.id or 0) % len(ACCENTS)]
    a, rtl = _bgr(accent), language.startswith("ar")
    big, mid = int(H * 0.11), int(H * 0.06)
    sp_name, sp_btn = (0, 0) if rtl else (2, 1)  # libass stops shaping Arabic when letter spacing ≠ 0
    end = f"0:00:{d:05.2f}"
    ass = work / "bumper.ass"
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Name,Arial Black,{big},&H00FFFFFF,&H00FFFFFF,&H00{a},&H80000000,1,0,0,0,100,100,{sp_name},0,1,{max(3, big // 14)},{max(2, big // 20)},5,40,40,0,1
Style: Sub,Arial Black,{mid},&H00FFFFFF,&H00FFFFFF,&H000000FF,&H000000FF,1,0,0,0,100,100,{sp_btn},0,3,{int(mid * 0.45)},0,5,40,40,0,1
Style: Like,Arial Black,{mid},&H00101010,&H00101010,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,{sp_btn},0,3,{int(mid * 0.45)},0,5,40,40,0,1
Style: Bar,Arial,{int(H * 0.012)},&H00{a},&H00{a},&H00{a},&H00{a},0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:00.10,{end},Name,,0,0,0,,{{\\pos({W // 2},{int(H * 0.36)})\\fad(120,250)\\fscx40\\fscy40\\t(0,220,\\fscx112\\fscy112)\\t(220,380,\\fscx100\\fscy100)}}{name}
Dialogue: 0,0:00:00.35,{end},Bar,,0,0,0,,{{\\pos({W // 2},{int(H * 0.47)})\\fad(0,250)\\p1\\fscx0\\t(0,350,\\fscx100)}}m 0 0 l {int(W * 0.5)} 0 {int(W * 0.5)} {int(H * 0.008)} 0 {int(H * 0.008)}
Dialogue: 2,0:00:00.75,{end},Sub,,0,0,0,,{{\\pos({W // 2 + (int(W * 0.17) if rtl else -int(W * 0.17))},{int(H * 0.62)})\\fad(80,250)\\fscx20\\fscy20\\t(0,180,\\fscx118\\fscy118)\\t(180,320,\\fscx100\\fscy100)}}{t['subscribe']}
Dialogue: 2,0:00:01.25,{end},Like,,0,0,0,,{{\\pos({W // 2 - (int(W * 0.17) if rtl else -int(W * 0.17))},{int(H * 0.62)})\\fad(80,250)\\fscx20\\fscy20\\t(0,180,\\fscx118\\fscy118)\\t(180,320,\\fscx100\\fscy100)}}{t['like']}
""", encoding="utf-8")
    ass_arg = filter_path(ass)
    # background: the channel's accent drifting over near-black, plus a whoosh under the voice
    run_ffmpeg("-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0=0x{accent}:c1=0x07090d:c2=0x07090d:n=3:speed=0.006:d={d:.2f}:r={fps}",
               "-f", "lavfi", "-i", f"anoisesrc=d=0.9:c=pink:a=0.5", "-i", str(voice),
               "-filter_complex",
               f"[0:v]eq=brightness=-0.12:saturation=1.2,ass='{ass_arg}',fade=t=out:st={d - 0.25:.2f}:d=0.25,format=yuv420p[v];"
               "[1:a]highpass=f=500,lowpass=f=4500,afade=t=in:d=0.35,afade=t=out:st=0.35:d=0.55,volume=0.18[w];"
               "[2:a]adelay=450|450,apad[s];"
               f"[w][s]amix=inputs=2:duration=longest:normalize=0,atrim=0:{d:.2f},aformat=sample_rates=48000:channel_layouts=stereo,"
               "loudnorm=I=-14:TP=-1.5:LRA=11[a]",
               "-map", "[v]", "-map", "[a]", "-t", f"{d:.2f}", "-c:v", H264, "-b:v", "12M",
               "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out))
    log.info("bumper ready for channel %s %s (%.1f s)", channel.id, language, d)
    return out.replace(final)


IN_FX, OUT_FX = ("zoomin", 0.35), ("circleopen", 0.45)  # intro → bumper, bumper → content (ffmpeg xfade)


def splice(final: Path, bumper: Path, at: float, out: Path, bitrate: str, fps: int) -> tuple[Path, float]:
    """final[0:at] → (zoom) → bumper → (circle reveal + whoosh) → final[at:], re-encoded once (captions are
    already burnt in). The audio crossfades into the bumper (the gap between scenes is silence) and back out
    with the content at full volume from its first word. Returns (out, seconds the video got longer)."""
    bd = duration(bumper)
    (fx1, d1), (fx2, d2) = IN_FX, OUT_FX
    d1 = min(d1, max(0.05, at - 0.05))
    o1 = at - d1
    o2 = o1 + bd - d2
    norm = f"fps={fps},format=yuv420p,setsar=1,settb=AVTB"
    aud = "aformat=sample_rates=48000:channel_layouts=stereo"
    run_ffmpeg("-i", str(final), "-i", str(bumper), "-f", "lavfi", "-i", "anoisesrc=d=0.7:c=pink:a=0.5", "-filter_complex",
               f"[0:v]trim=0:{at:.3f},setpts=PTS-STARTPTS,{norm}[v0];[0:a]atrim=0:{at:.3f},asetpts=PTS-STARTPTS,{aud}[a0];"
               f"[1:v]setpts=PTS-STARTPTS,{norm}[v1];[1:a]asetpts=PTS-STARTPTS,{aud}[a1];"
               f"[0:v]trim=start={at:.3f},setpts=PTS-STARTPTS,{norm}[v2];[0:a]atrim=start={at:.3f},asetpts=PTS-STARTPTS,{aud}[a2];"
               f"[v0][v1]xfade=transition={fx1}:duration={d1:.3f}:offset={o1:.3f}[x1];"
               f"[x1][v2]xfade=transition={fx2}:duration={d2:.3f}:offset={o2:.3f}[v];"
               f"[a0][a1]acrossfade=d={d1:.3f}:c1=tri:c2=tri[y1];[y1][a2]acrossfade=d={d2:.3f}:c1=exp:c2=nofade[y2];"
               # a soft air sweep, well under the voice (0.4 at 500 Hz–9 kHz read as a burst of static)
               f"[2:a]highpass=f=700,lowpass=f=4500,afade=t=in:d=0.3,afade=t=out:st=0.3:d=0.4,volume=0.12,{aud},"
               f"adelay={int(max(0, o2 - 0.2) * 1000)}|{int(max(0, o2 - 0.2) * 1000)}[wh];"
               "[y2][wh]amix=inputs=2:duration=first:normalize=0[a]",
               "-map", "[v]", "-map", "[a]", "-c:v", H264, "-b:v", bitrate, "-profile:v", "high",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out))
    return out, bd - d1 - d2
