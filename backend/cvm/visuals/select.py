"""Pick a visual for every scene of the project's primary-language script (shared by all languages)."""

import contextvars
import logging
import re
from pathlib import Path

from sqlmodel import Session, select

from cvm import progress
from cvm.config import settings
from cvm.models import Asset, Channel, Project, Trend
from cvm.paths import project_dir
from cvm.visuals import flags, sources
from cvm.voice.narrate import latest_script

log = logging.getLogger(__name__)
AVATAR_TYPES = {"avatar_full", "avatar_pip"}
# seed shift for the judge's redraws when the validation step (cvm/curation.py) sent the visuals back
REDO_SHIFT: contextvars.ContextVar[int] = contextvars.ContextVar("visuals_redo_shift", default=0)
GENERIC = ("presenter", "host", "talking to camera", "talking head", "anchor", "person speaking", "vlogger")


def _topic_terms(scenes: list[dict]) -> list[str]:
    """Search terms of the script's supporting scenes — what the video is about."""
    terms = [t for sc in scenes if sc["visual"]["type"] not in AVATAR_TYPES | {"chart"} for t in sc["visual"].get("search_terms") or []]
    return [t for t in dict.fromkeys(terms) if not any(g in t.lower() for g in GENERIC)]


def _avatar_backup(scene: dict, topic_terms: list[str], topic: str | None) -> dict:
    """Avatar scenes (hook, CTA) speak about the whole video. While no lipsync engine is installed they become an
    image — so it should picture the overall subject: the writer's image_prompt, else FLUX key art of the topic.
    Stock search only uses the scene's own topical terms (borrowing another scene's terms showed e.g. a 1931
    subpoena under the CTA)."""
    own = [t for t in scene["visual"].get("search_terms") or [] if not any(g in t.lower() for g in GENERIC)]
    subject = ", ".join(x for x in [topic, *own[:2]] if x) or ", ".join(topic_terms[:1])
    prompt = scene["visual"].get("image_prompt") or (f"{subject}, cinematic key art, dramatic lighting, no text" if subject else None)
    return {**scene, "visual": {**scene["visual"], "search_terms": own or topic_terms[:1], "image_prompt": prompt}}


def _broll(scene: dict, portrait: bool, dest: Path, used: set[str] | None = None) -> dict | None:
    v = scene["visual"]
    cands = sources.search_broll(v.get("search_terms") or [scene.get("on_screen_text") or ""], portrait)
    used = used if used is not None else set()
    cands = [c for c in cands if c["url"] not in used]  # one shot per video: no repeated images across scenes
    for c in cands[:3]:
        try:
            path = str(sources.download(c["url"], dest))
            used.add(c["url"])
            return {**c, "path": path, "candidates": cands[:8]}
        except Exception as e:  # noqa: BLE001
            log.warning("download failed %s: %s", c["url"], e)
    return None


def _flag_image(scene: dict, portrait: bool, dest: Path) -> dict | None:
    """A flag scene with a known country: the official flag (Wikimedia Commons), laid flat on a frame."""
    is_flag, code = flags.scene_flag(scene)
    if not (is_flag and code):
        return None
    out = flags.official_flag(code, dest / f"flag_{code}_{'portrait' if portrait else 'landscape'}.png", portrait)
    if not out:
        return None
    return {"kind": "image", "path": str(out), "source": flags.SOURCE, "license": flags.LICENSE, "flag": code}


def _ai_image(scene: dict, portrait: bool, dest: Path, kids: bool = False) -> dict | None:
    if flags.scene_flag(scene)[0]:  # AI models draw national flags wrong: never ask them for one
        return _flag_image(scene, portrait, dest)  # None (unknown country / offline) → the chain goes to real stock
    prompt = scene["visual"].get("image_prompt") or ", ".join(scene["visual"].get("search_terms") or [])
    kids = kids or bool(scene["visual"].get("_kids"))  # kids series stay on FLUX (same look every episode)
    prefer = scene["visual"].get("_engine")  # unless the channel picked its engine (Channel.prefs.image_engine)
    if not prompt or not (engine := sources.image_engine(kids, prefer)):
        return None
    import hashlib

    # named by prompt (and seed, for takes redrawn by the judge): a new prompt never reuses an older image
    seed = scene["visual"].get("_seed")
    # Klein series: the sheets of the characters in the scene (cvm/visuals/cast.py); a redrawn sheet is a new image
    cast_refs = scene["visual"].get("_cast") or [] if engine == "klein" else []
    if cast_refs:
        from cvm.visuals import cast

        refs = [Path(p) for _, p in cast_refs]
        prompt = cast.prompt_with_refs(prompt, [(n, Path(p)) for n, p in cast_refs])
        prompt_id = prompt + "|" + "|".join(f"{p.name}:{p.stat().st_mtime_ns}" for p in refs if p.exists())
    else:
        refs, prompt_id = None, prompt
    tag = hashlib.sha1(f"{prompt_id}{'|' + str(seed) if scene['visual'].get('_redo') else ''}".encode()).hexdigest()[:8]
    out = dest / f"{engine.replace('-', '')}_scene_{scene['index']:02d}_{tag}.png"
    try:
        source = sources.SOURCE_LABEL[engine]
        if not out.exists():  # kids: a fixed seed per series keeps the characters closer
            source = sources.ai_image(prompt, out, portrait, seed=seed, kids=kids, prefer=prefer, refs=refs)
        return {"kind": "image", "path": str(out), "source": source, "license": "generated", "prompt": prompt}
    except Exception as e:  # noqa: BLE001
        log.warning("AI image failed: %s", e)
        return None


def _headline(scene: dict, sources_urls: list[str], portrait: bool, dest: Path, used: set[str] | None = None) -> dict | None:
    """Try the scene's article, then the script's other sources (sites behind bot walls are skipped).
    An article already shown in another scene is skipped, so two headline scenes show two outlets."""
    used = used if used is not None else set()
    urls = [u for u in [scene["visual"].get("source_url"), *sources_urls] if u and u.startswith("http") and u not in used]
    out = dest / f"headline_scene_{scene['index']:02d}.png"
    for url in dict.fromkeys(urls):
        try:
            sources.news_screenshot(url, out, portrait)
            used.add(url)
            return {"kind": "screenshot", "path": str(out), "source": url, "license": "news excerpt (credit outlet)",
                    "credit": url.split("/")[2].removeprefix("www.")}
        except Exception as e:  # noqa: BLE001
            log.warning("screenshot failed %s: %s", url, e)
    return None


def _source_clip(scene: dict, dest: Path) -> dict | None:
    url = scene["visual"].get("source_url")
    if not url:
        return None
    try:
        start = float((scene["visual"].get("notes") or "0").split("start=")[-1].split()[0]) if "start=" in (scene["visual"].get("notes") or "") else 0
        path = sources.source_clip(url, dest / "clips", start=start, length=min(scene.get("target_seconds") or 10, 10))
        return {"kind": "video", "path": str(path), "source": url, "license": "excerpt for commentary (credit creator)"}
    except Exception as e:  # noqa: BLE001
        log.warning("yt-dlp failed %s: %s", url, e)
        return None


def _footage(scene: dict, dest: Path, channel, used: set[str]) -> dict | None:
    from cvm.visuals.footage import find_footage

    return find_footage(scene, dest, bool(channel and channel.allow_official_clips), used)


def _article_image(scene: dict, sources_urls: list[str], dest: Path, channel, used: set[str]) -> dict | None:
    """Opt-in per channel: lead image of the script's articles (each article once), credited to the outlet."""
    if not (channel and channel.allow_article_images):
        return None
    urls = [u for u in [scene["visual"].get("source_url"), *sources_urls] if u and u.startswith("http") and f"img:{u}" not in used]
    for url in dict.fromkeys(urls):
        try:
            img = sources.article_image(url)
            if img:
                path = str(sources.download(img["url"], dest))
                used.add(f"img:{url}")
                return {**img, "path": path}
        except Exception as e:  # noqa: BLE001
            log.warning("article image failed %s: %s", url, e)
    return None


DRAWN = re.compile(r"\b(?:(?:flat|simple|minimalist|cute|stylized|2d)\s+)*(?:vector\s+)?(?:illustration|illustrated|drawing|"
                   r"cartoon|clip\s?art|doodle|icon|3d\s+render|vector\s+art|flat\s+design)(?:\s+style)?(?:\s+of)?\b|"
                   r"\b(?:in\s+(?:a\s+)?)?(?:flat|vector|cartoon)\s+style\b", re.IGNORECASE)


def photographic(scene: dict) -> tuple[dict, bool]:
    """Outside kids episodes a real subject is never drawn (user, 2026-11-23: a flat-vector sand cat and cartoon rhinos
    in the safari video): an illustration-style prompt loses its style words and becomes a documentary photograph,
    and the caller tries real stock first. Returns (scene, was_drawn)."""
    v = scene["visual"]; prompt = v.get("image_prompt") or ""
    if v.get("_kids") or not DRAWN.search(prompt):
        return scene, False
    clean = re.sub(r"\s{2,}", " ", re.sub(r"\s*,\s*,+", ", ", DRAWN.sub("", prompt))).strip(" ,.")
    clean = re.sub(r"^(?:a|an)\s+", "", clean)
    clean = (clean + ", " if clean else "") + "documentary photograph, natural light, 35mm lens, realistic"
    return {**scene, "visual": {**v, "image_prompt": clean}}, True


def pick(scene: dict, sources_urls: list[str], portrait: bool, dest: Path, topic_terms: list[str] | None = None,
         used: set[str] | None = None, topic: str | None = None, channel=None) -> dict:
    used = used if used is not None else set()
    t = scene["visual"]["type"]
    if t in AVATAR_TYPES and scene["visual"].get("media_id") is not None and (got := _user_media(scene)):
        return {"type": t, **got}  # the presenter over the user's own file (the script's or media mode "mine")
    if t in AVATAR_TYPES:
        scene = _avatar_backup(scene, topic_terms or [], topic)
    scene, drawn = photographic(scene)
    if t in AVATAR_TYPES | {"broll", "ai_image"} and all(flags.scene_flag(scene)) and (got := _flag_image(scene, portrait, dest)):
        return {"type": t, **got}  # a known country's flag: the official one beats stock (which may show another flag)
    chain = {
        "user_media": [lambda: _user_media(scene), lambda: _broll(scene, portrait, dest, used), lambda: _ai_image(scene, portrait, dest)],
        "broll": [lambda: _broll(scene, portrait, dest, used), lambda: _ai_image(scene, portrait, dest)],
        "ai_image": [lambda: _ai_image(scene, portrait, dest), lambda: _broll(scene, portrait, dest, used)],
        "footage": [lambda: _footage(scene, dest, channel, used), lambda: _article_image(scene, sources_urls, dest, channel, used),
                    lambda: _broll(scene, portrait, dest, used), lambda: _ai_image(scene, portrait, dest)],
        "headline": [lambda: _headline(scene, sources_urls, portrait, dest, used), lambda: _article_image(scene, sources_urls, dest, channel, used),
                     lambda: _broll(scene, portrait, dest, used)],
        "source_clip": [lambda: _source_clip(scene, dest), lambda: _broll(scene, portrait, dest, used)],
        "chart": [],  # rendered as a text card by the composer
        # backup when the avatar can't talk yet (see _avatar_backup)
        "avatar_full": [lambda: _ai_image(scene, portrait, dest), lambda: _broll(scene, portrait, dest, used)],
        "avatar_pip": [lambda: _ai_image(scene, portrait, dest), lambda: _broll(scene, portrait, dest, used)],
    }[t]
    if drawn and t in AVATAR_TYPES | {"ai_image"}:  # a drawing was asked for a real subject: real stock first
        chain = [lambda: _broll(scene, portrait, dest, used), lambda: _ai_image(scene, portrait, dest)]
    if scene["visual"].get("_kids") and t != "user_media":
        # kids series are 100% drawn: never stock photos (on 2026-11-18 FLUX stopped mid-episode and 506 got real
        # photos of real children plus ~100 blank text cards); a failed drawing fails the step, to be retried
        chain = [lambda: _ai_image(scene, portrait, dest)]
    for attempt in chain:
        got = attempt()
        if got:
            return {"type": t, **got}
    if scene["visual"].get("_kids") and t != "user_media":
        raise RuntimeError(f"desenho da cena {scene['index']} falhou (FLUX); as cenas já desenhadas ficam guardadas, "
                           "tente de novo")
    return {"type": t, "kind": "card"}  # text card fallback, always renderable


def _apply_media_mode(scene: dict, mode: str, mine: list[int], used_mine: list[int]) -> dict:
    """"online": the user's files are ignored; "mine": every non-avatar, non-chart scene shows one of them
    (the script's choice when it made one, else the least used file, in order)."""
    v = scene["visual"]
    if mode == "online" and v["type"] == "user_media":
        return {**scene, "visual": {**v, "type": "broll" if v.get("search_terms") else "ai_image"}}
    if mode == "mine" and mine and v["type"] in AVATAR_TYPES:
        # the presenter speaks over the user's own footage, never over an AI drawing (a GTA Short showed a cartoon
        # face and the brief's words drawn as text behind him, 2026-11-20): the script's file, else the least used
        mid = v.get("media_id") if v.get("media_id") in mine else min(mine, key=lambda m: (used_mine.count(m), mine.index(m)))
        used_mine.append(mid)
        return {**scene, "visual": {**v, "media_id": mid}}
    if mode == "mine" and mine and v["type"] not in AVATAR_TYPES and v["type"] != "chart":
        mid = v.get("media_id") if v["type"] == "user_media" and v.get("media_id") in mine else \
            min(mine, key=lambda m: (used_mine.count(m), mine.index(m)))
        used_mine.append(mid)
        return {**scene, "visual": {**v, "type": "user_media", "media_id": mid}}
    if v["type"] == "user_media" and v.get("media_id") in mine:
        used_mine.append(v["media_id"])
    return scene


def _user_media(scene: dict) -> dict | None:
    """One of the user's own files, chosen by the scriptwriter (visual.media_id)."""
    from cvm.db import engine

    mid = scene["visual"].get("media_id")
    if mid is None:
        return None
    with Session(engine) as s:
        m = s.get(Asset, int(mid))
        if not m or m.kind != "user_media" or not Path(m.path).exists():
            return None
        video = m.meta["kind"] == "video"
        start = float(scene["visual"].get("media_start") or 0) if video else 0.0
        if video and m.meta.get("duration"):
            start = min(max(0.0, start), max(0.0, m.meta["duration"] - 1))
        if video and m.meta.get("segments"):
            from cvm.models import Project
            from cvm.visuals.highlights import segment_start

            from cvm.visuals.highlights import ranges_apply

            p = s.get(Project, m.project_id)
            if p and ranges_apply(m.meta, p):  # best moments, or from–to ranges set by hand
                start = segment_start(m.meta, start)
        return {"kind": m.meta["kind"], "path": m.path, "source": "seu arquivo", "license": "próprio", "media_id": m.id,
                "hold_last_frame": video, "start": start, "fit": video}


def ai_video_ready(kids: bool = False) -> bool:
    from cvm.models_registry import MLX, is_ready

    if not MLX:  # CUDA: this machine's animation tier, when downloaded (cvm/visuals/video_cuda.py)
        from cvm import engines
        from cvm.visuals import video_cuda

        return (kids or settings.ai_video_max_clips > 0) and video_cuda.ready(engines.feature_engine("animation"))

    wan = is_ready("wan-ti2v-5b") and (settings.models_dir / "MLXGen" / ".venv" / "bin" / "mlxgen").exists()
    return (kids or settings.ai_video_max_clips > 0) and (sources.ltx_ready() or wan)


HOLD = " The subject stays in place inside the frame for the whole shot; nothing leaves the frame; the camera barely moves."


def _animate(scene: dict, chosen: dict, seconds: float, portrait: bool, dest: Path) -> dict | None:
    """Turn a scene's AI still into a short clip (LTX when installed, else Wan 2.2); the still remains the
    fallback. Clips are cached per engine, image, motion and length.
    A clip that loses its subject (`clip_rank.steadiness` under STEADY: the character jumps out of the frame, the
    drawing turns into another scene) is made once more with another seed and a "hold the subject" line; when
    that take drifts too, the scene keeps its still (the composer moves the camera over it)."""
    import hashlib
    import time

    from cvm.visuals import clip_rank

    motion = scene["visual"]["motion_prompt"]
    from cvm import engines

    purpose = "kids" if scene["visual"].get("_kids") else \
        "cartoon" if DRAWN.search(scene["visual"].get("image_prompt") or "") else "realistic"
    from cvm.models_registry import MLX

    if MLX:
        engine = "ltx" if engines.video_engine(purpose) == "ltx" and sources.ltx_ready() else "wan"
    else:  # CUDA: the tier picked for this card (ltxv-2b, wan-5b…); clips of another tier are never reused
        engine = engines.feature_engine("animation") or "none"
    steps = f"|{settings.ai_video_steps}" if engine == "wan" else ""
    key = hashlib.sha1(f"{chosen['path']}|{motion}|{seconds:.1f}{steps}".encode()).hexdigest()[:8]
    check = clip_rank.available()
    try:
        from cvm.visuals import video_cuda

        name = {"ltx": f"ltx-{sources.ltx_version()}" if MLX else "ltx", "wan": "wan2.2-ti2v-5b",
                **video_cuda.LABELS}.get(engine, engine)
        for take, (suffix, seed, extra) in enumerate((("", 42, ""), ("_b", 43, HOLD))):
            out = dest / f"{engine}_scene_{scene['index']:02d}_{key}{suffix}.mp4"
            t = time.time()
            if not out.exists():
                _, name = sources.animate_clip(Path(chosen["path"]), f"{motion}. Keep the composition, lighting and palette of the input frame.{extra}",
                                               out, seconds, portrait, settings.ai_video_steps, seed=seed, purpose=purpose)
            steady = clip_rank.steadiness(out) if check else None
            log.info("%s clip scene %s in %.0fs (steadiness %s)", name, scene["index"], time.time() - t, steady)
            if steady is None or steady >= clip_rank.STEADY:
                return {**chosen, "kind": "video", "path": str(out), "source": f"{name} (local)", "still": chosen["path"],
                        "still_source": chosen.get("source"),
                        "motion_prompt": motion, "hold_last_frame": True, **({"steady": steady} if steady is not None else {})}
            log.info("scene %s: the clip loses its subject (%.2f < %.2f), %s", scene["index"], steady, clip_rank.STEADY,
                     "keeping the still" if take else "making it again")
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("animation failed on scene %s, keeping the still: %s", scene["index"], e)
        return None


def _scene_seconds(session: Session, project: Project, lang: str) -> dict[int, float]:
    from cvm.voice.narrate import narration_asset

    narr = narration_asset(session, project.id, lang)
    return {t["index"]: t["end"] - t["start"] for t in (narr.meta.get("scenes", []) if narr else [])}


def _sharpen(picked: list[dict], n: int) -> list[dict]:
    """SeedVR2 upscale of the FLUX stills that made it into the edit (768 px → 1080 px short side, ~20 s each).
    Clips are left alone: frame by frame it costs ~20 s per frame. The original stays in meta["original"]."""
    from cvm.visuals import upscale

    stills = [c for c in picked if c.get("kind") == "image" and sources.is_ai_still(c.get("source"))
              and c.get("path") and Path(c["path"]).exists()]
    if not stills or not upscale.ready():
        return picked
    progress.report(n, n + 1, f"melhorando nitidez de {len(stills)} imagens")
    done = upscale.upscale([Path(c["path"]) for c in stills], 1080)
    return [{**c, "path": done[c["path"]], "original": c["path"]} if c.get("path") in done else c for c in picked]


def source_visuals(session: Session, project: Project, portrait: bool = True) -> list[Asset]:
    lang = project.languages[0]
    script = latest_script(session, project.id, lang)
    if not script:
        raise RuntimeError("no script")
    dest = project_dir(project.id) / "visuals"
    dest.mkdir(exist_ok=True)
    mode = project.media_mode or "mix"
    mine = [a.id for a in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "user_media")
                                       .order_by(Asset.id)).all()
            # best moments: a video with nothing worth keeping (all repeated or dull) is never filled in
            if not (project.media_cut == "highlights" and a.meta.get("kind") == "video"
                    and a.meta.get("segments") == [])] if mode != "online" else []
    used_mine: list[int] = []
    kept = {}  # scenes the user set to one of their own files keep it when visuals are redone
    for old in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "visual")):
        if (old.meta or {}).get("media_id") is not None:
            kept[old.scene] = old.meta["media_id"]
        session.delete(old)
    assets = []
    topic_terms, used = _topic_terms(script.content["scenes"]), set()
    trend = session.get(Trend, project.trend_id) if project.trend_id else None
    # the subject in a few words: a topic can be a whole brief (rules, timelines), which an image model draws as text
    topic = trend.title if trend else (project.title or (project.topic or "").split("\n")[0][:120])
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    series = session.get(Channel, project.series_id) if project.series_id else channel  # kids: the series' look
    # supporting scenes first, so avatar backups don't take the shots their own scenes asked for
    order = sorted(script.content["scenes"], key=lambda sc: sc["visual"]["type"] in AVATAR_TYPES)
    kids = project.style == "kids"
    # kids episodes go to a 16:9 YouTube channel: draw and animate landscape instead of cropping portrait art
    shape = portrait and not (kids and channel and channel.platform.value == "youtube")
    animate_left = (settings.kids_max_clips if kids else settings.ai_video_max_clips) if ai_video_ready(kids) else 0
    seconds = _scene_seconds(session, project, lang) if animate_left else {}
    picked = []
    sheets = False
    if kids:
        from cvm.visuals import cast

        if sheets := cast.uses_sheets(series):  # a Klein series: each new character's reference sheet is drawn once
            progress.report(0, len(order), "desenhando as fichas dos personagens")
            cast.ensure_sheets(session, series, script.content.get("cast_looks") or {})
    for i, scene in enumerate(order):
        progress.report(i, len(order), f"cena {i + 1}/{len(order)}")
        if kids:  # one seed per series (channel) keeps the characters' look closer across scenes and episodes
            scene["visual"]["_seed"] = 7000 + (project.series_id or project.channel_id or project.id)
            scene["visual"]["_kids"] = True
            scene["visual"]["_engine"] = ((series.prefs or {}).get("image_engine") if series else None)
            if sheets:
                scene["visual"]["_cast"] = [(n, str(p)) for n, p in cast.scene_cast(series, scene)]
        if scene["index"] in kept:
            scene = {**scene, "visual": {**scene["visual"], "type": "user_media", "media_id": kept[scene["index"]]}}
        else:
            scene = _apply_media_mode(scene, mode, mine, used_mine)
        chosen = pick(scene, script.content.get("sources", []), shape, dest, topic_terms, used, topic, channel)
        if animate_left and scene["visual"].get("motion_prompt") and sources.is_ai_still(chosen.get("source")) \
                and not chosen.get("flag") and not flags.scene_flag(scene)[0]:  # a flag is never redrawn by LTX/Wan
            if anim := _animate(scene, chosen, min(seconds.get(scene["index"], 4.0), 5.0), shape, dest):
                chosen, animate_left = {"type": chosen["type"], **anim}, animate_left - 1
        picked.append({**chosen, "_scene": scene["index"]})
    from cvm.visuals import judge

    if judge.ready():  # anti-slop: Gemma 3 vision checks every AI still/clip; failures redrawn or swapped
        progress.report(len(order), len(order) + 1, "conferindo imagens de IA")
        by_index = {sc["index"]: sc for sc in order}

        def redo(sc: dict, seed: int) -> dict | None:
            if flags.scene_flag(sc)[0]:  # never redraw a flag scene with AI
                return None
            return _ai_image({**sc, "visual": {**sc["visual"], "_seed": seed + REDO_SHIFT.get(), "_redo": True}}, shape, dest, kids)

        picked = judge.review(picked, by_index, redo, lambda sc: _broll(sc, shape, dest, used), kids, dest / "judge")
    picked = _sharpen(picked, len(order))
    for chosen in picked:
        a = Asset(project_id=project.id, kind="visual", scene=chosen["_scene"], path=chosen.get("path", ""),
                  source=chosen.get("source"), license=chosen.get("license"),
                  meta={k: v for k, v in chosen.items() if k not in ("path", "_scene")})
        session.add(a)
        assets.append(a)
    session.commit()
    return sorted(assets, key=lambda a: a.scene)
