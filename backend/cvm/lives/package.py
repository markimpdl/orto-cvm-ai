"""One live → the day's package: one "melhores momentos" long video + N Shorts, each Short pinned to one moment.

Split for the cut editor (cvm/lives/cuts.py): analyse_recording (+ chat_density) runs at analysis time and
create_projects at "Produzir aprovados" with the owner's cuts; make_package is the old automatic path (both at once),
kept for Channel.prefs.auto_approve_cuts. Shorts (owner, 2026-11-25): editorial vertical layout, no avatar on
screen; by default only the cut with its own sound (Channel.prefs.shorts_commentary / per-cut `commentary` turns his
commentary on: another creator's sound then plays low under it); an own live on a channel with a marked facecam keeps
the camera-over-game stack when commented.

All projects are style "live" (skill script-style-live) on the live's channel, with only the user's own media
(media_mode "mine", media_cut "highlights") and the live's own sound kept. The long project's media gets the
segments found by cvm/visuals/highlights.py; every Short gets a copy of that media row whose only segment is its
moment (segments_auto False, so a recount never moves it). Shorts reuse the long row's meta (preview, duration):
the recording is analysed and probed once.
"""

import logging
from pathlib import Path

from sqlmodel import Session

from cvm.models import Asset, Channel
from cvm.suggest import new_project

log = logging.getLogger(__name__)


PROXY_MIN_S = 1800  # recordings longer than this are analysed on a light 360p H.264 copy (one decode of the original)
LONG_MIN_S = 300  # shorter sources give Shorts only (no "melhores momentos" video)
LANGS = {"en": "en-US", "pt": "pt-BR", "ar": "ar-AE"}  # the source video's language → whisper's


def analysis_source(video: Path, duration: float, min_s: float = PROXY_MIN_S) -> Path:
    """The file the best-moments analysis reads. A multi-hour AV1/VP9 live decoded four times beside the rest of the
    queue failed silently once (Speed's 6 h live came out "all dark"); a 360p H.264 proxy is decoded once, fast and
    reliably, and keeps the same timeline. Kept next to the recording and reused."""
    if duration < min_s:
        return video
    import subprocess

    from cvm.config import settings

    proxy = video.with_name(video.stem + "_proxy360.mp4")
    if not (proxy.exists() and proxy.stat().st_size > 0):
        tmp = proxy.with_suffix(".part.mp4")
        r = subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(video), "-vf", "scale=-2:360",
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-c:a", "aac", "-b:a", "96k",
                            str(tmp)], capture_output=True, text=True)
        if r.returncode != 0 or not tmp.exists():
            raise RuntimeError(f"não consegui preparar a gravação para análise: {r.stderr[-300:]}")
        tmp.replace(proxy)
    return proxy


NO_COMMENTARY = ("SEM COMENTÁRIO (pedido do dono no editor de cortes): este Short é só o trecho (~{length} s) com o "
                 "som original, sem narração por cima e sem avatar. Todas as cenas com narration \"\" e "
                 "visual.keep_audio=true, na ordem do trecho; packaging.header {{kicker, headline com UMA palavra entre "
                 "*asteriscos*}} no topo e legendas da fala original; on_screen_text vazio; sem pedido de inscrição. ")


def shorts_commentary(channel: Channel | None) -> bool:
    """Channel.prefs.shorts_commentary: a new cut's default in the editor and of the automatic path. Off by default
    (owner, 2026-11-25): a Short from "Clips e cortes" is only the cut, its own sound, editorial header + captions."""
    return ((channel.prefs or {}) if channel else {}).get("shorts_commentary") is True


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def analyse_recording(video: Path, live: dict, source: Path | None = None) -> tuple[float, dict]:
    """(duration, highlights.analyse result) of the recording; a recording that can't be read or has no usable
    moment raises (nothing is created). `source`: an already made light copy with the same timeline."""
    from cvm.api.media import _duration
    from cvm.visuals import highlights

    dur = float(_duration(video) or live.get("duration") or 0)
    r = highlights.analyse([source or analysis_source(video, dur)], [dur])[0]
    if not r["segments"]:
        raise RuntimeError("nenhum momento aproveitável na gravação (tudo escuro, parado ou repetido)")
    return dur, r


def source_language(live: dict, channel: Channel) -> str:
    """The recording's spoken language as a video language (pt-BR / en-US / ar-AE), else the channel's."""
    return LANGS.get(str(live.get("language") or "")[:2], channel.language)


def chat_density(live: dict, video: Path) -> dict[int, float]:
    """{second: weighted messages} of a live's chat replay ({} when there is none or it can't be read)."""
    from cvm.lives import chat

    try:
        path = chat.fetch(live["url"], video.parent, str(live.get("video_id") or video.stem))
        return chat.density(path) if path else {}
    except Exception as e:  # noqa: BLE001 — the analysis goes on without the chat
        log.warning("chat replay unavailable: %s", e)
        return {}


def make_package(session: Session, channel: Channel, live: dict, video: Path, n_shorts: int | None = None,
                 third_party: dict | None = None, pick=None, examples: list[str] | None = None, min_shorts: int = 0,
                 taste: list[str] | None = None, detect_layout=None) -> dict:
    """The automatic path (no cut editor: Channel.prefs.auto_approve_cuts, or a fixed n_shorts): analyse, pick the
    moments, create the projects. third_party={uploader, url, channel_id} for another creator's video: style "react"
    (the avatar comments on the moments: never a plain repost, YouTube's reused-content rule), credit on screen and
    in the description, and the 🟡 risk mark in the topic/title."""
    from cvm.visuals import highlights

    # analyse first: a recording that can't be read or has no usable moment creates nothing
    dur, r = analyse_recording(video, live)
    source_lang = source_language(live, channel)
    if n_shorts is not None:  # fixed count asked by the caller: loudness rule
        moments, method = highlights.top_moments(r.get("moments") or r["segments"], n_shorts), "volume"
    else:  # how many and which: viral potential (transcript + Claude), else the loudness rule
        from cvm.lives import viral

        kw = {"examples": examples, **({"min_count": min_shorts} if min_shorts else {}), **({"taste": taste} if taste else {})}
        live_s = float(live.get("duration") or dur)
        meta = {"moments": r.get("moments", []), "segments": r["segments"], "tracks": r.get("tracks")}
        if live.get("url") and live_s >= 600:  # a live's chat replay says where the audience reacted most
            from cvm.lives import chat

            dens = chat_density(live, video)
            if dens:
                meta = {**meta, "moments": chat.boost(meta.get("moments") or [], dens, live_s)}
        moments, method = (pick or viral.pick_moments)(video, meta, live["title"], not third_party, source_lang,
                                                       duration=live_s, **kw)
    # the streamer's camera, as in the cut editor (owner, 2026-11-26: the automatic path made Shorts without the
    # 10% header / 40% camera / 50% game layout because only cuts.analyse looked for the camera)
    layout = (detect_layout or auto_layout)(channel, video, float(live.get("duration") or dur), not third_party)
    return create_projects(session, channel, live, video, r, moments, method, third_party, dur, layout=layout)


def auto_layout(channel: Channel, video: Path, duration: float, own: bool) -> dict:
    """{facecam, facecam_order} for the automatic package: the channel's box for an own recording, else the webcam
    found by cvm/lives/facecam.py; {} when none is found (plain editorial layout) or detection fails."""
    from cvm.lives.cuts import find_layout
    from cvm.render.stack import order_of, validate

    try:
        found = find_layout(channel, video, duration, own=own)
        box = found.get("facecam")
        if not box:
            return {}
        return {"facecam": validate(box), "facecam_order": order_of(found.get("facecam_order"))}
    except Exception as e:  # noqa: BLE001 — the Shorts are still made, in the plain editorial layout
        log.warning("facecam detection for %s failed: %s", video, e)
        return {}


def create_projects(session: Session, channel: Channel, live: dict, video: Path, r: dict, moments: list[dict],
                    method: str, third_party: dict | None = None, dur: float = 0.0, make_long: bool = True,
                    layout: dict | None = None) -> dict:
    """The package's projects: the long "melhores momentos" (own recordings ≥ LONG_MIN_S, when make_long) and one
    Short per moment. A moment may carry the owner's `comment` from the cut editor: it goes into that Short's topic
    (the scriptwriter reads it through get_project) and leads its script. layout: {facecam, facecam_order} from the
    cut editor (cuts.layout_of) — written on every Short's recording row, so an editorial Short is stacked as
    streamer camera + header + gameplay (cvm/render/stack.py)."""
    src_layout = dict(layout or {})
    from cvm.api.media import attach_file

    style = "react" if third_party else "live"
    who = (third_party or {}).get("uploader") or "outro criador"
    credit = (f" Vídeo de {who} ({(third_party or {}).get('url')}): 🟡 conteúdo de outro criador — o avatar comenta cada "
              "momento (nunca só repostar), crédito ao criador na tela e na descrição.") if third_party else ""
    mark = "🟡 " if third_party else ""
    # presenter "scenes": the avatar only in its own comment scenes, never over the whole cut (faster: LTX only for
    # those seconds; and in a react it never sits on screen while the other creator is talking)
    base = {"channel_id": channel.id, "style": style, "languages": [channel.language], "media_mode": "mine",
            "media_cut": "highlights", "force_similar": True, "presenter": "scenes"}
    src = f" ({live['url']})" if live.get("url") else ""
    held: dict = {}
    # a 35 s clip gives Shorts only; and long videos are the user's own work only (his rule, 2026-11-21):
    # another creator's video never becomes a long "melhores momentos"
    make_long = make_long and float(live.get("duration") or dur) >= LONG_MIN_S and not third_party

    def attach_source(project) -> None:
        """First project of the package: the recording itself (probed once); later ones reuse its row's meta."""
        a = attach_file(session, project.id, video, video.name, f"gravação “{live['title']}”" + (f" de {who}" if third_party else ""),
                        keep_audio=True)
        if third_party:
            a.source, a.license = f"{who} ({third_party.get('url')})", "terceiro — crédito ao criador"
        a.meta = {**a.meta, "segments": r["segments"], "segments_auto": True, "whole": r["whole"],
                  "dropped": r["dropped"], "moments": r.get("moments", [])}
        session.add(a)
        session.commit()
        held["asset"] = a

    long_p = None
    if make_long:
        long_p = new_project(session, {**base, "title": f"{mark}Melhores momentos: {live['title']}", "platforms": ["youtube"],
                                       "topic": f"Melhores momentos da live “{live['title']}”{src}. Vídeo longo de 10 a 15 "
                                                "minutos só com os melhores trechos da gravação, na ordem, com o som "
                                                "original da live; gancho curto no começo e capítulos por momento." + credit},
                             before_job=attach_source)
        la = held["asset"]
    foreign = bool(third_party) and source_language(live, channel) != channel.language
    from cvm.grow.discover import prompts
    from cvm.render.stack import facecam_of

    lines = prompts()
    # Shorts layout (owner, 2026-11-25): the editorial vertical (header on top, clip below, word captions, no avatar
    # on screen; the avatar stays as the narrator's voice) for every Short, except an own live on a channel with a
    # marked facecam, which keeps the camera-over-game stack (cvm/render/stack.py)
    stack = not third_party and facecam_of(channel) is not None
    shorts = []
    for i, m in enumerate(moments, 1):
        seg = {"start": m["start"], "end": m["end"]}
        commentary = shorts_commentary(channel) if m.get("commentary") is None else bool(m["commentary"])
        editorial = not stack or not commentary
        if not commentary:  # just the clip with its own sound: nothing narrated over it
            voice = {"keep_audio": True, "audio_under": False}
        elif third_party:  # his commentary over the other creator's clip, whose sound plays low underneath
            voice = {"keep_audio": False, "audio_under": True}
        else:
            voice = {}

        def attach_short(project, seg=seg) -> None:
            if "asset" not in held:  # Shorts-only package: the first Short attaches (and probes) the recording
                attach_source(project)
                a = held["asset"]
                a.meta = {**a.meta, "segments": [seg], "segments_auto": False, "whole": False, "moments": [], **voice,
                          **src_layout}
                session.add(a)
                session.commit()
                return
            src_row = held["asset"]
            session.add(Asset(project_id=project.id, kind="user_media", path=src_row.path, source=src_row.source,
                              license=src_row.license, meta={**src_row.meta, "segments": [seg], "segments_auto": False,
                                                             "whole": False, "moments": [], **voice, **src_layout}))
            session.commit()

        viral_note = (f" Gancho sugerido: {m.get('hook') or '(nenhum)'}. Por que pode viralizar: {m.get('why') or '-'}"
                      + (f" (nota {m['viral']}/10)." if m.get("viral") is not None else ".")
                      if m.get("hook") or m.get("why") else "")
        if str(m.get("comment") or "").strip():  # the owner's note from the cut editor leads this Short's script
            viral_note += (" Instrução do dono para este corte (vale mais que as sugestões acima): "
                           f"{str(m['comment']).strip()[:1500]}")
        length = round(m["end"] - m["start"])
        if not commentary:
            how = NO_COMMENTARY.format(length=length)
        elif foreign:
            how = ("O vídeo original é em outra língua: a fala dele não toca. O apresentador (só a voz) comenta e "
                   "explica, em cima das imagens desse trecho, o que o criador está fazendo, mostrando ou ensinando, do "
                   "começo ao fim, na língua do canal (45–58 s), sem mandar o público buscar a resposta no canal dele. "
                   "Só a reação crua dele no clímax (grito, risada, comemoração, até 4 s, sem frase para entender) pode "
                   "tocar com visual.keep_audio=true. ")
        else:
            how = f"Só esse trecho (~{length} s), com o som original; "
        layout = ""
        if editorial and commentary:
            layout = lines["editorial"] + (lines["third"] if third_party else "")
        p = new_project(session, {**base, "presenter": "editorial" if editorial else "scenes",
                                  "title": f"{mark}Short {i}: {live['title']}", "platforms": ["youtube_shorts"],
                                  "topic": layout + f"Short do momento {_fmt(m['start'])}–{_fmt(m['end'])} da live "
                                           f"“{live['title']}”{src}. " + how +
                                           "siga as regras de Short viral do estilo live." + viral_note + credit},
                        before_job=attach_short)
        shorts.append(p.id)
    return {"long": long_p.id if long_p else None, "shorts": shorts, "moments": [{"start": m["start"], "end": m["end"]} for m in moments],
            "method": method}
