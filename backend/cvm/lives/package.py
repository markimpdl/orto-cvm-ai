"""One live → the day's package: one "melhores momentos" long video + N Shorts, each Short pinned to one moment.

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


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def make_package(session: Session, channel: Channel, live: dict, video: Path, n_shorts: int | None = None,
                 third_party: dict | None = None, pick=None, examples: list[str] | None = None, min_shorts: int = 0) -> dict:
    """third_party={uploader, url, channel_id} for another creator's video: style "react" (the avatar comments on
    the moments: never a plain repost, YouTube's reused-content rule), credit on screen and in the description, and
    the 🟡 risk mark in the topic/title."""
    from cvm.api.media import _duration, attach_file
    from cvm.visuals import highlights

    # analyse first: a recording that can't be read or has no usable moment creates nothing
    dur = float(_duration(video) or live.get("duration") or 0)
    r = highlights.analyse([analysis_source(video, dur)], [dur])[0]
    if not r["segments"]:
        raise RuntimeError("nenhum momento aproveitável na gravação (tudo escuro, parado ou repetido)")

    n = n_shorts  # None: as many Shorts as the analysis finds strong moments (highlights.top_moments)
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
    make_long = float(live.get("duration") or dur) >= LONG_MIN_S and not third_party

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
    else:  # no long project: the Shorts get the analysis meta straight from the recording
        la = type("Row", (), {"meta": {"segments": r["segments"], "moments": r.get("moments", []), "keep_audio": True}})()
    source_lang = LANGS.get(str(live.get("language") or "")[:2], channel.language)
    foreign = bool(third_party) and source_lang != channel.language
    if n is not None:  # fixed count asked by the caller: loudness rule
        moments, method = highlights.top_moments(r.get("moments") or r["segments"], n), "volume"
    else:  # how many and which: viral potential (transcript + Claude), else the loudness rule
        from cvm.lives import viral

        kw = {"examples": examples, **({"min_count": min_shorts} if min_shorts else {})}
        live_s = float(live.get("duration") or dur)
        meta = la.meta
        if live.get("url") and live_s >= 600:  # a live's chat replay says where the audience reacted most
            from cvm.lives import chat

            try:
                path = chat.fetch(live["url"], video.parent, str(live.get("video_id") or video.stem))
                dens = chat.density(path) if path else {}
            except Exception as e:  # noqa: BLE001 — the analysis goes on without the chat
                log.warning("chat replay unavailable: %s", e)
                dens = {}
            if dens:
                meta = {**meta, "moments": chat.boost(meta.get("moments") or [], dens, live_s)}
        moments, method = (pick or viral.pick_moments)(video, meta, live["title"], not third_party, source_lang,
                                                       duration=live_s, **kw)
    shorts = []
    for i, m in enumerate(moments, 1):
        seg = {"start": m["start"], "end": m["end"]}
        # a source in another language (an English tutorial on the pt-BR channel): its voice is not played, the
        # presenter explains what it shows/teaches over the footage, on screen the whole time (user, 2026-11-22)
        voice = {"keep_audio": False} if foreign else {}

        def attach_short(project, seg=seg) -> None:
            if "asset" not in held:  # Shorts-only package: the first Short attaches (and probes) the recording
                attach_source(project)
                a = held["asset"]
                a.meta = {**a.meta, "segments": [seg], "segments_auto": False, "whole": False, "moments": [], **voice}
                session.add(a)
                session.commit()
                return
            src_row = held["asset"]
            session.add(Asset(project_id=project.id, kind="user_media", path=src_row.path, source=src_row.source,
                              license=src_row.license, meta={**src_row.meta, "segments": [seg], "segments_auto": False,
                                                             "whole": False, "moments": [], **voice}))
            session.commit()

        viral_note = (f" Gancho sugerido: {m['hook']}. Por que pode viralizar: {m['why']} (nota {m['viral']}/10)."
                      if m.get("hook") or m.get("why") else "")
        p = new_project(session, {**base, "title": f"{mark}Short {i}: {live['title']}", "platforms": ["youtube_shorts"],
                                  "topic": f"Short do momento {_fmt(m['start'])}–{_fmt(m['end'])} da live "
                                           f"“{live['title']}”{src}. " + (
                                               "O vídeo original é em outra língua: a fala dele não toca. O apresentador "
                                               "comenta e explica, em cima das imagens desse trecho, o que o criador está "
                                               "fazendo, mostrando ou ensinando, do começo ao fim, na língua do canal "
                                               "(45–58 s), sem mandar o público buscar a resposta no canal dele. Só a "
                                               "reação crua dele no clímax (grito, risada, comemoração, até 4 s, sem "
                                               "frase para entender) pode tocar com visual.keep_audio=true. " if foreign else
                                               "Só esse trecho, de 7 a 45 s, com o som original; ") +
                                           "siga as regras de Short viral do estilo live." + viral_note + credit},
                        before_job=attach_short)
        shorts.append(p.id)
    return {"long": long_p.id if long_p else None, "shorts": shorts, "moments": [{"start": m["start"], "end": m["end"]} for m in moments],
            "method": method}
