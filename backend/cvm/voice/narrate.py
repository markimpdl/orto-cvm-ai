"""Narrate an approved script; scene timings come straight from the audio lengths.

Each scene is synthesised one sentence at a time (numbers spelled out first), with edge silence trimmed and
fixed pauses between sentences and scenes, so the rhythm doesn't depend on whatever silence the TTS left.
Every take is checked by a whisper round trip; a take that skipped, repeated or garbled words is regenerated
with another seed (the best of MAX_TAKES is kept). Takes are cached by text+voice+seed, so editing one line of
the script only re-synthesises that sentence.
"""

import contextvars
import hashlib
import logging
from pathlib import Path

import numpy as np
import soundfile as sf
from sqlmodel import Session, col, select

from cvm import progress
from cvm.models import Asset, Channel, Project, Script, VoiceProfile
from cvm.paths import project_dir
from cvm.voice import characters, qa
from cvm.voice.engine import synthesize
from cvm.voice.text import prepare, spell_numbers, split_sentences

log = logging.getLogger(__name__)

SR = 24000
SENTENCE_GAP_S = 0.25
GAP_S = 0.35  # breath between scenes
MAX_TAKES = 3
MAX_WER = 0.3
MAX_S_PER_CHAR = 0.16  # far slower than any natural read → the model kept talking/hallucinating
CACHE_VERSION = 1
# Cloning a voice into another language (e.g. an Arabic reference reading English) makes Chatterbox drawl.
# Such takes are sped up toward a natural pace (characters per second of speech), never more than MAX_TEMPO.
NATURAL_CPS = {"en-US": 14.5, "pt-BR": 14.0, "ar-AE": 12.0}
MAX_TEMPO = 1.3
# the intro (scenes up to script.intro_end) is read hyped: more emotion, quicker pace, shorter pauses
INTRO_VOICE = {"exaggeration": 0.8, "cfg_weight": 0.35}
INTRO_SENTENCE_GAP_S, INTRO_GAP_S = 0.12, 0.2
# sentence → seed shift for takes the validation step (cvm/curation.py) sent back: other seeds, so other takes
REDO: contextvars.ContextVar[dict] = contextvars.ContextVar("narrate_redo", default={})


def latest_script(session: Session, project_id: int, language: str) -> Script | None:
    return session.exec(
        select(Script)
        .where(Script.project_id == project_id, Script.language == language)
        .order_by(col(Script.version).desc())
    ).first()


def voice_variants(profiles: list[VoiceProfile]) -> list[dict]:
    """An avatar's named voices (VoiceProfile.params.variant/label, e.g. "real" = a recorded
    voice, "ia" = the avatar's original synthetic one), in stable profile order for display. The channel/project policy,
    never this ordering, defines the product default."""
    out: dict[str, dict] = {}
    for p in sorted(profiles, key=lambda p: p.id or 0):
        v = (p.params or {}).get("variant")
        if v and p.ref_audio_path:
            e = out.setdefault(v, {"key": v, "label": (p.params or {}).get("label") or v, "languages": []})
            e["languages"].append(p.language)
    return list(out.values())


def voice_profile(session: Session, avatar_id: int | None, language: str,
                  variant: str | None = None) -> VoiceProfile | None:
    """The avatar's reference for `language`. With `variant` (Project.voice_variant), only that named voice:
    its own reference in this language, else its reference in another language (cloned across languages).
    Without a valid variant this low-level helper deterministically falls back to the oldest same-language profile,
    then the oldest profile in any language; product UIs must resolve or explicitly ask for a voice first."""
    if not avatar_id:
        return None
    profiles = sorted(session.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == avatar_id)).all(),
                      key=lambda p: p.id or 0)
    if variant and any((p.params or {}).get("variant") == variant for p in profiles):
        mine = [p for p in profiles if (p.params or {}).get("variant") == variant and p.ref_audio_path]
        same = [p for p in mine if p.language == language]
        if same or mine:
            return (same or mine)[0]
    same = [p for p in profiles if p.language == language and p.ref_audio_path]
    return (same or [p for p in profiles if p.ref_audio_path] or [None])[0]


PARAGRAPH_CHARS = 420  # ≈ 25–30 s: the trained voice reads whole paragraphs this long fluently (user, by ear, 2026-11-14)


def paragraphs(sentences: list[str], limit: int = PARAGRAPH_CHARS) -> list[str]:
    """Consecutive sentences joined into takes of at most `limit` characters (a longer sentence stays alone)."""
    out: list[str] = []
    for sent in sentences:
        if out and len(out[-1]) + 1 + len(sent) <= limit:
            out[-1] += " " + sent
        else:
            out.append(sent)
    return out


def scene_groups(texts: list[tuple[int, str, object]], limit: int = PARAGRAPH_CHARS) -> list[list[int]]:
    """Consecutive scenes read as ONE take by the trained voice: (scene index, text, key) in order, where scenes join
    only while their key matches (same voice, intro or not, same delivery; key None = never joined, e.g. a scene
    playing the user's own audio) and the joined text stays within `limit`. In Shorts every scene is a 2–4 s
    sentence; read one by one, the prosody restarts every few seconds (the sentence-by-sentence read the user
    rejected by ear on 2026-11-14), so the trained voice reads across scenes and the take is cut back per scene."""
    out: list[list[int]] = []
    size, last_key = 0, None
    for idx, text, key in texts:
        if out and key is not None and key == last_key and size + 1 + len(text) <= limit:
            out[-1].append(idx)
            size += 1 + len(text)
        else:
            out.append([idx])
            size = len(text)
        last_key = key
    return out


def split_points(texts: list[str], words: list[dict], duration: float, language: str) -> list[float]:
    """Seconds where a take reading `texts` back to back is cut into one piece per text: in the pause between the
    last word heard of one text and the first of the next (whisper word times `words` = [{"w","s","e"}], aligned to
    the expected words), else at the text's share of the characters. len(texts) - 1 points, increasing."""
    from difflib import SequenceMatcher

    exp, owner = [], []
    for i, t in enumerate(texts):
        toks = qa._tokens(t, language)
        exp += toks
        owner += [i] * len(toks)
    heard, times = [], []
    for w in words:
        for tok in qa._tokens(w["w"], language):
            heard.append(tok)
            times.append((float(w["s"]), float(w["e"])))
    match = {}  # expected token index → heard token index
    for a, b, n in SequenceMatcher(None, exp, heard, autojunk=False).get_matching_blocks():
        for k in range(n):
            match[a + k] = b + k
    total = sum(len(t) for t in texts) or 1
    points, chars = [], 0
    for i in range(len(texts) - 1):
        chars += len(texts[i])
        guess = duration * chars / total
        ends = [times[match[j]][1] for j, o in enumerate(owner) if o == i and j in match]
        starts = [times[match[j]][0] for j, o in enumerate(owner) if o == i + 1 and j in match]
        if ends and starts and starts[0] >= ends[-1]:
            guess = (ends[-1] + starts[0]) / 2
        lo = points[-1] + 0.2 if points else 0.2
        points.append(min(max(guess, lo), max(lo, duration - 0.2)))
    return points


def quietest(audio: np.ndarray, at: float, window: float = 0.3) -> float:
    """The quietest 20 ms point within ±window s of `at` (so a cut never lands inside a word)."""
    f = int(0.02 * SR)
    a, b = max(0, int((at - window) * SR)), min(len(audio), int((at + window) * SR))
    if b - a < 2 * f:
        return at
    seg = audio[a:b]
    n = len(seg) // f
    rms = np.sqrt((seg[: n * f].reshape(n, f) ** 2).mean(1))
    i = j = int(np.argmin(rms))
    quiet = rms <= rms[i] * 1.5 + 1e-6  # the middle of that quiet stretch, not its first frame
    while i > 0 and quiet[i - 1]:
        i -= 1
    while j + 1 < n and quiet[j + 1]:
        j += 1
    return (a + (i + j + 1) * f // 2) / SR


def trained_voice(profile: VoiceProfile | None) -> bool:
    """A voice fine-tuned on the owner's own recordings (cvm/voice/finetune.py registers it with a LoRA / MLX model)."""
    params = (profile.params or {}) if profile else {}
    return bool(params.get("lora") or params.get("mlx_model"))


def eleven_voice(profile: VoiceProfile | None) -> bool:
    """A voice read by ElevenLabs (paid by the user, cvm/voice/eleven.py)."""
    return bool(profile and (profile.params or {}).get("engine") == "elevenlabs")


ELEVEN_TAKES = 2  # each retake spends credits: one retry at most


def _rms(audio: np.ndarray) -> float:
    a = audio[np.abs(audio) > 1e-3]  # speech only, not the pauses
    return float(np.sqrt(np.mean(a ** 2))) if len(a) else 0.0


def _plain(s: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def _mix_plan(session: Session, project: Project, language: str, script: Script, pron: dict,
              trained: VoiceProfile | None) -> tuple[dict[int, list[tuple[str, bool]]], VoiceProfile | None]:
    """Mixed voice (user, 2026-11-23): {scene: [(text, by_eleven)]} for the scenes with a sentence holding an acronym
    or an English word — consecutive plain sentences stay one trained paragraph, the foreign ones go to ElevenLabs —
    and the ElevenLabs profile. ({}, None) when ElevenLabs is off, unaffordable this month or the language is not
    pt-BR / en-US: the whole video is then read by the trained voice."""
    from cvm.voice import eleven
    from cvm.voice.text import has_foreign

    if language not in ("pt-BR", "en-US") or not trained_voice(trained):
        return {}, None
    alt = voice_profile(session, voice_avatar(session, project), language, "eleven")
    if not eleven_voice(alt) or not eleven.available():
        return {}, None
    plan: dict[int, list[tuple[str, bool]]] = {}
    chars = 0
    for sc in script.content.get("scenes", []):
        if sc.get("speaker") or not (sc.get("narration") or "").strip():
            continue
        raw = split_sentences(sc["narration"], language)
        flags = [has_foreign(x, language) for x in raw]
        if not any(flags):
            continue
        chunks: list[tuple[str, bool]] = []
        for x, f in zip(raw, flags):
            if chunks and chunks[-1][1] == f:
                chunks[-1] = (chunks[-1][0] + " " + x, f)
            else:
                chunks.append((x, f))
        # ElevenLabs says English names right as written: the respellings made for the trained voice ("Dáuntaun",
        # "Xeique Zaied Rôud") are left out of its sentences; Portuguese respellings (e.g. "Júlia") still apply
        own = {k: v for k, v in (pron or {}).items() if _plain(k) == _plain(v)}  # only accent marks added
        plan[sc["index"]] = [(prepare(x, language, own if f else pron, trained=not f), f) for x, f in chunks]
        chars += sum(len(x) for x, f in plan[sc["index"]] if f)
    if not plan:
        return {}, alt
    if not eleven.fits(int(chars * 1.35) + 200):
        log.warning("project %s: mixed voice needs %d ElevenLabs characters, not available this month", project.id, chars)
        return {}, None
    return plan, alt


def _mixed_scene(chunks: list[tuple[str, bool]], language: str, trained: VoiceProfile, alt: VoiceProfile, cache: Path,
                 seed: int, check: bool, report: list, engines: set, index: int, trained_rms: list[float],
                 intro: bool) -> list[np.ndarray]:
    """One scene read in turns: trained paragraphs and ElevenLabs sentences, each ElevenLabs piece brought to the
    trained voice's level (this scene's trained pieces, else the video's so far) so the switch doesn't jump."""
    takes: list[tuple[np.ndarray, bool]] = []
    for k, (text, by_eleven) in enumerate(chunks):
        voice = alt if by_eleven else trained
        ctx = (chunks[k - 1][0] if k else None, chunks[k + 1][0] if k + 1 < len(chunks) else None) if by_eleven else None
        try:
            audio, engine, info = _best_take(text, language, voice, cache, seed if not by_eleven else seed + 31, check,
                                             context=ctx)
        except Exception as e:  # noqa: BLE001 — ElevenLabs down for one sentence: the trained voice reads it
            if not by_eleven:
                raise
            log.warning("mixed voice: ElevenLabs failed (%s), trained voice reads: %s", e, text[:60])
            audio, engine, info = _best_take(text, language, trained, cache, seed, check)
            info = {**info, "eleven_failed": str(e)[:120]}
            by_eleven = False
        engines.add(engine)
        report.append({"scene": index, "sentence": k, "text": text, "voice": "eleven" if by_eleven else "trained", **info})
        takes.append((audio, by_eleven))
    local = [_rms(a) for a, e in takes if not e]
    target = float(np.median(local or trained_rms or [0.0]))
    out: list[np.ndarray] = []
    for k, (audio, by_eleven) in enumerate(takes):
        if by_eleven and target > 0 and (r := _rms(audio)) > 0:
            audio = np.clip(audio * (target / r), -1.0, 1.0)
        if out:
            out.append(np.zeros(int((INTRO_SENTENCE_GAP_S if intro else SENTENCE_GAP_S) * SR), dtype="float32"))
        out.append(audio.astype("float32"))
    return out


def _eleven_or_fallback(session: Session, project: Project, language: str, profile: VoiceProfile | None,
                        script: Script) -> tuple[VoiceProfile | None, str | None]:
    """An ElevenLabs profile only when the whole narration fits this month's credits (with room for retakes);
    otherwise the WHOLE video is read by the trained voice (else the avatar's default) — never half and half."""
    if not eleven_voice(profile):
        return profile, None
    from cvm.voice import eleven

    chars = sum(len(sc.get("narration") or "") for sc in script.content.get("scenes", []) if not sc.get("speaker"))
    if eleven.available() and eleven.fits(int(chars * 1.35) + 200):
        return profile, None
    why = "sem chave do ElevenLabs" if not eleven.available() else "créditos do ElevenLabs insuficientes este mês"
    fallback = voice_profile(session, voice_avatar(session, project), language, "real")
    if not trained_voice(fallback):
        fallback = voice_profile(session, voice_avatar(session, project), language, None)
    log.warning("project %s %s: %s, reading with %s", project.id, language, why, "trained voice" if trained_voice(fallback) else "default voice")
    return fallback, why


def voice_avatar(session: Session, project: Project) -> int | None:
    """Whose voice reads the video: the project's avatar, else — for a video with no avatar on screen (presenter
    "editorial", user 2026-11-23: "sem meu avatar, só com comentários") — the channel's avatar. Hiding the avatar must
    never hide the voice (663 went out in macOS' default voice when its avatar was removed)."""
    if project.avatar_id:
        return project.avatar_id
    from cvm.models import Channel

    ch = session.get(Channel, project.channel_id) if project.channel_id else None
    return (ch.avatar_id or (ch.prefs or {}).get("avatar_id")) if ch else None


def project_variant(session: Session, project: Project) -> str | None:
    """The video's own voice choice, else the channel's default voice (Channel.prefs.voice_variant, e.g. "real" = the
    trained voice) when the video uses the channel's avatar. A variant this PC doesn't have falls back to the default."""
    if project.voice_variant:
        return project.voice_variant
    from cvm.models import Channel

    ch = session.get(Channel, project.channel_id) if project.channel_id else None
    if ch and (ch.prefs or {}).get("voice_variant") and voice_avatar(session, project) in (ch.avatar_id, (ch.prefs or {}).get("avatar_id")):
        return ch.prefs["voice_variant"]
    return None


def checked_profile(session: Session, avatar_id: int | None, language: str, variant: str | None) -> VoiceProfile | None:
    """voice_profile, but a video that asks for the trained voice ("real") never gets the AI clone instead
    (user's rule, 2026-11-22): no trained profile for the avatar → the step fails and says so."""
    profile = voice_profile(session, avatar_id, language, variant)
    if variant == "real" and not trained_voice(profile):
        raise RuntimeError(f"voz treinada não encontrada para {language}; a voz clonada não é usada")
    return profile


def narrate(session: Session, project: Project, language: str) -> Asset:
    script = latest_script(session, project.id, language)
    if not script:
        raise RuntimeError(f"no script for {language}")
    variant = project_variant(session, project)
    mix = variant == "mix"  # trained voice + ElevenLabs only for sentences with acronyms / English words
    profile = checked_profile(session, voice_avatar(session, project), language, "real" if mix else variant)
    if profile is None and variant:  # the channel has its own voice: never read it with macOS' default voice instead
        raise RuntimeError(f"voz do canal ({variant}) não encontrada para {language}; a narração não usa a voz padrão do Mac")
    profile, voice_fallback = _eleven_or_fallback(session, project, language, profile, script)
    out_dir = project_dir(project.id, language) / "voice"
    out_dir.mkdir(exist_ok=True)

    cache = out_dir / "takes"
    cache.mkdir(exist_ok=True)
    seed0 = (profile.id if profile else 0) * 7919 + 17
    check = qa_available()
    pieces, timings, t, engines, report = [], [], 0.0, set(), []
    channel = session.get(Channel, project.channel_id) if project.channel_id else None
    from cvm.channels import series_of

    series = series_of(session, project) or channel  # kids: the series keeps its cast and voices on any channel
    # respellings: the series' and the channel's (prefs.pronunciation, e.g. "Cacau" → "Cacáu"), the script's win
    pron = {**((series.prefs or {}).get("pronunciation") or {} if series else {}),
            **((channel.prefs or {}).get("pronunciation") or {} if channel else {}),
            **(script.content.get("pronunciation") or {})}
    sentences = {sc["index"]: split_sentences(prepare(sc["narration"], language, pron, trained=trained_voice(profile)), language) for sc in script.content["scenes"]}
    mixed, alt = _mix_plan(session, project, language, script, pron, profile) if mix else ({}, None)
    if mix and not alt:
        voice_fallback = voice_fallback or "mix: ElevenLabs indisponível, só voz treinada"
    total, done = sum(map(len, sentences.values())), 0
    cast = script.content.get("cast") or {}
    intro_end = script.content.get("intro_end")
    intro_voice = ((channel.prefs or {}).get("intro_voice") if channel else None) or INTRO_VOICE
    joined, tails = _cross_scene_takes(session, script, series, cast, profile, sentences, intro_end, language, cache, seed0,
                                check, intro_voice, report, engines, skip=set(mixed))
    trained_rms: list[float] = []  # level of the trained takes, so ElevenLabs sentences sit at the same loudness
    for scene in script.content["scenes"]:
        if scene["index"] in joined:  # read together with its neighbours by the trained voice, cut back per scene
            audio = joined[scene["index"]]
            done += len(sentences[scene["index"]])
            progress.report(done, total, f"{language}: frase {done}/{total}")
            intro = intro_end is not None and scene["index"] <= intro_end
            sf.write(out_dir / f"scene_{scene['index']:02d}.wav", audio, SR)
            dur = len(audio) / SR
            timings.append({"index": scene["index"], "start": round(t, 3), "end": round(t + dur, 3)})
            gap = 0.0 if scene["index"] not in tails else INTRO_GAP_S if intro and scene["index"] != intro_end else GAP_S
            pieces += [audio, np.zeros(int(gap * SR), dtype="float32")]
            t += dur + gap
            continue
        intro = intro_end is not None and scene["index"] <= intro_end
        scene_audio = []
        # kids series: a character's line is spoken with that character's own (saved) voice
        speaker = characters.profile_for(session, series, scene.get("speaker"), language, cast) if scene.get("speaker") \
            else characters.narrator_for(series, language)  # a series' own narrator voice beats the avatar's
        if not speaker and not scene.get("speaker") and not profile:  # no avatar either: design the series narrator
            speaker = characters.profile_for(session, series, "Narrador", language, cast)
        voice, seed = (speaker, speaker.id * 7919 + 17) if speaker else (profile, seed0)
        parts = sentences[scene["index"]]
        orig = original_audio(session, scene)
        if orig is not None:  # the user's own recorded speech for this part: nothing synthesised
            done += len(parts)
            parts = []
            report.append({"scene": scene["index"], "sentence": 0, "text": "(áudio original do vídeo)", "original": True})
        elif scene["index"] in mixed and not scene.get("speaker"):  # trained voice + ElevenLabs on the foreign bits
            scene_audio = _mixed_scene(mixed[scene["index"]], language, profile, alt, cache, seed, check, report,
                                       engines, scene["index"], trained_rms, intro)
            done += len(parts)
            parts = []
        elif trained_voice(voice) or eleven_voice(voice):  # one flowing paragraph (prosody resets between sentences)
            parts = paragraphs(parts)
        for k, sentence in enumerate(parts):
            progress.report(done, total, f"{language}: frase {done + 1}/{total}")
            done += len(split_sentences(sentence, language)) if len(parts) != len(sentences[scene["index"]]) else 1
            style = scene.get("delivery") or script.content.get("voice_style")
            context = None
            if eleven_voice(voice):  # the neighbouring text, so ElevenLabs carries the intonation across takes
                order = [sc["index"] for sc in script.content["scenes"]]
                pos = order.index(scene["index"])
                prev = parts[k - 1] if k else (" ".join(sentences[order[pos - 1]]) if pos else None)
                nxt = parts[k + 1] if k + 1 < len(parts) else (" ".join(sentences[order[pos + 1]]) if pos + 1 < len(order) else None)
                context = (prev, nxt)
            audio, engine, info = _best_take(sentence, language, voice, cache, seed, check, style,
                                             intro_voice if intro else None, context=context)
            engines.add(engine)
            report.append({"scene": scene["index"], "sentence": k, "text": sentence, **info})
            if scene_audio:
                scene_audio.append(np.zeros(int((INTRO_SENTENCE_GAP_S if intro else SENTENCE_GAP_S) * SR), dtype="float32"))
            scene_audio.append(audio)
        audio = orig if orig is not None else np.concatenate(scene_audio) if scene_audio else np.zeros(int(0.5 * SR), dtype="float32")
        if orig is None and scene_audio and trained_voice(voice) and scene["index"] not in mixed:
            trained_rms.append(_rms(audio))
        sf.write(out_dir / f"scene_{scene['index']:02d}.wav", audio, SR)
        dur = len(audio) / SR
        timings.append({"index": scene["index"], "start": round(t, 3), "end": round(t + dur, 3), **({"original_audio": True} if orig is not None else {})})
        gap = INTRO_GAP_S if intro and scene["index"] != intro_end else GAP_S  # a full breath before the bumper
        pieces += [audio, np.zeros(int(gap * SR), dtype="float32")]
        t += dur + gap

    narration = out_dir / "narration.wav"
    sf.write(narration, np.concatenate(pieces), SR)
    for old in session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "narration", Asset.language == language)):
        session.delete(old)
    asset = Asset(
        project_id=project.id,
        kind="narration",
        language=language,
        path=str(narration),
        source=",".join(sorted(engines)),
        meta={"scenes": timings, "duration": round(t, 3), "script_version": script.version, "voice_profile_id": profile.id if profile else None,
              **({"voice_fallback": voice_fallback} if voice_fallback else {}),
              "qa": {"checked": check, "flagged": [r for r in report if r.get("flag")], "sentences": len(report)}},
    )
    session.add(asset)
    session.commit()
    return asset


def _cross_scene_takes(session, script, series, cast, profile, sentences, intro_end, language, cache, seed0, check,
                       intro_voice, report, engines, skip: set[int] | None = None) -> tuple[dict[int, np.ndarray], set[int]]:
    """({scene index: audio}, last scene of each take) for the scenes the trained avatar voice reads together with
    their neighbours (scene_groups); scenes left out are narrated one by one as before. Inside a take nothing is
    trimmed or added: glued back, its scenes are the take as it was read (its own pauses), so only the take's last
    scene gets the scene gap (user, 2026-11-22: "para ficar mais natural")."""
    if not trained_voice(profile):
        return {}, set()
    texts = []
    for scene in script.content["scenes"]:
        idx = scene["index"]
        text = " ".join(sentences[idx])
        own = (scene.get("speaker") or characters.narrator_for(series, language)
               or original_audio(session, scene) is not None or not text.strip() or idx in (skip or set()))
        intro = intro_end is not None and idx <= intro_end
        style = scene.get("delivery") or script.content.get("voice_style")
        texts.append((idx, text, None if own else (intro, style)))
    by_idx = {i: (t, k) for i, t, k in texts}
    out: dict[int, np.ndarray] = {}
    tails: set[int] = set()
    for group in scene_groups(texts):
        if len(group) < 2:
            continue
        parts = [by_idx[i][0] for i in group]
        intro, style = by_idx[group[0]][1]
        whole = " ".join(parts)
        audio, engine, info = _best_take(whole, language, profile, cache, seed0, check, style,
                                         intro_voice if intro else None)
        engines.add(engine)
        report.append({"scene": group[0], "scenes": group, "sentence": 0, "text": whole, **info})
        dur = len(audio) / SR
        try:
            from cvm.render.captions import words_from_whisper

            tmp = cache / "_split.wav"
            sf.write(tmp, audio, SR)
            words = words_from_whisper(tmp, language) if check else []
        except Exception as e:  # noqa: BLE001 — no word times: cut at the characters' share
            log.warning("word times unavailable for the scene cut: %s", e)
            words = []
        cuts = []
        for p in split_points(parts, words, dur, language):
            c = quietest(audio, p, window=0.15 if words else 0.3)
            cuts.append(min(max(c, (cuts[-1] if cuts else 0) + 0.1), dur - 0.1))
        bounds = [0, *[int(c * SR) for c in cuts], len(audio)]
        for k, i in enumerate(group):
            out[i] = audio[bounds[k]:bounds[k + 1]]
        tails.add(group[-1])
    return out, tails


ORIGINAL_RMS = 0.07  # the user's recorded speech brought near the synthetic voice's level


def original_audio(session: Session, scene: dict) -> np.ndarray | None:
    """A scene showing one of the user's videos marked "keep the original audio" (the file's `keep_audio`, or the
    scene's `visual.keep_audio` when set) plays that part of the clip's own sound instead of a narration: from the
    same second the picture starts (select._user_media) for `target_seconds` (else to the end of the clip)."""
    import subprocess

    from cvm.config import settings

    v = scene.get("visual") or {}
    if v.get("type") != "user_media" or v.get("media_id") is None:
        return None
    m = session.get(Asset, int(v["media_id"]))
    if not m or m.kind != "user_media" or (m.meta or {}).get("kind") != "video":
        return None
    keep = v["keep_audio"] if v.get("keep_audio") is not None else (m.meta or {}).get("keep_audio")
    if not keep or not Path(m.path).exists():
        return None
    start = float(v.get("media_start") or 0)  # the same second the picture starts (select._user_media)
    if m.meta.get("duration"):
        start = min(max(0.0, start), max(0.0, m.meta["duration"] - 1))
    p = session.get(Project, m.project_id)
    from cvm.visuals.highlights import ranges_apply

    if p and ranges_apply(m.meta, p):
        from cvm.visuals.highlights import segment_start

        start = segment_start(m.meta, start)
    left = max(0.5, float(m.meta.get("duration") or 0) - start) if m.meta.get("duration") else None
    if p and ranges_apply(m.meta, p):
        from cvm.visuals.highlights import segment_end

        if (end := segment_end(m.meta, start)) is not None:  # a Short stays inside its moment
            left = max(0.5, min(left or 1e9, end - start))
    length = min(float(scene.get("target_seconds") or left or 30), left or 1e9)
    proc = subprocess.run([str(settings.ffmpeg_bin), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", m.path,
                           "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    audio = np.frombuffer(proc.stdout, dtype="float32").copy() if proc.returncode == 0 else np.zeros(0, dtype="float32")
    if len(audio) < int(length * SR):  # no sound track / short read: silence, so the picture still plays whole
        audio = np.concatenate([audio, np.zeros(int(length * SR) - len(audio), dtype="float32")])
    rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
    if rms > 1e-4:
        audio = audio * min(ORIGINAL_RMS / rms, 0.95 / max(float(np.abs(audio).max()), 1e-6))
    return audio.astype("float32")


def narration_asset(session: Session, project_id: int, language: str) -> Asset | None:
    return session.exec(
        select(Asset).where(Asset.project_id == project_id, Asset.kind == "narration", Asset.language == language)
    ).first()


def scene_paths(asset: Asset) -> list[Path]:
    d = Path(asset.path).parent
    return sorted(d.glob("scene_*.wav"))


def qa_available() -> bool:
    from cvm.models_registry import is_ready

    return is_ready("whisper")


def _take(sentence: str, language: str, profile: VoiceProfile | None, cache: Path, seed: int,
          style: str | None = None, tone: dict | None = None, context: tuple | None = None) -> tuple[np.ndarray, str]:
    from cvm.voice.engine import wanted_engine

    key = hashlib.sha1(f"{CACHE_VERSION}|{language}|{profile.id if profile else 0}|{wanted_engine(profile)}|{style}|"
                       f"{profile.ref_audio_path if profile else ''}|{seed}|{sentence}{f'|{sorted(tone.items())}' if tone else ''}".encode()).hexdigest()[:16]
    wav = cache / f"{key}.wav"
    eng_file = wav.with_suffix(".engine")
    if wav.exists() and eng_file.exists() and eng_file.read_text() == "macos" and profile and profile.ref_audio_path:
        wav.unlink()  # a fallback take from a failed cloning run: try the real voice again
    if not wav.exists():
        _, engine = synthesize(sentence, language, profile, wav, seed=seed, instruct=style, tone=tone, context=context)
        eng_file.write_text(engine)
    audio, sr = sf.read(wav, dtype="float32")
    assert sr == SR, sr
    audio = qa.trim_edges(audio, SR)
    if profile and profile.language and profile.language != language:
        audio = _natural_pace(audio, sentence, language)
    return audio, (eng_file.read_text() if eng_file.exists() else "voxcpm2")


def _natural_pace(audio: np.ndarray, sentence: str, language: str) -> np.ndarray:
    """Speed up a drawled cross-language take (pitch kept, ffmpeg atempo) so it sounds like a natural read."""
    import subprocess
    import tempfile

    from cvm.config import settings

    seconds = len(audio) / SR
    cps = len(sentence) / seconds if seconds else 0
    target = NATURAL_CPS.get(language, 14.0)
    if not cps or cps >= target * 0.92:
        return audio
    tempo = min(MAX_TEMPO, target / cps)
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.wav", Path(tmp) / "out.wav"
        sf.write(src, audio, SR)
        subprocess.run([settings.ffmpeg_bin, "-v", "error", "-y", "-i", str(src), "-af", f"atempo={tempo:.3f}", "-ar", str(SR),
                        str(dst)], check=True, capture_output=True)
        out, _ = sf.read(dst, dtype="float32")
    return out


def _score(audio: np.ndarray, sentence: str, language: str, cache: Path) -> dict:
    tmp = cache / "_qa.wav"
    sf.write(tmp, audio, SR)
    heard, words = qa.heard_words(tmp, language)
    # words the model made up after the sentence ended (643 scene 25: "...do passeio inteiro. Tiff e..."): cut off
    # when a pause separates them, else the take is bad; all the expected words being there no longer hides them
    edges = qa.edge_extras(sentence, words, language)
    if edges.get("cut_at"):
        audio = qa.trim_edges(audio[:int(edges["cut_at"] * SR)], SR)
        sf.write(tmp, audio, SR)
        heard, words = qa.heard_words(tmp, language)
    errors, n = qa.word_errors(sentence, spell_numbers(heard, language), language)
    wer = errors / n if n else 0.0
    too_long = len(audio) / SR > max(2.0, len(sentence) * MAX_S_PER_CHAR)
    # two slips are tolerated: whisper itself rewrites short phrases ("por quê" → "o porquê")
    # VoxCPM2 often garbles or drops a sentence's FIRST word ("Se inscreve" → "Ans inscreve"): one slip there is a
    # bad take even when the rest is perfect
    first = qa.first_word_ok(sentence, heard, language)
    noise = qa.noise_bursts(audio, SR)  # hiss whisper can't hear (no words in it)
    glued = bool(edges.get("tail") and not edges.get("cut_at"))
    bad = (errors > max(2, MAX_WER * n) or too_long or qa.loop_artifact(heard, language) or not first or bool(noise)
           or glued or bool(edges.get("lead")))
    return {"wer": round(wer, 3), "heard": heard, "too_long": too_long, "bad": bad, "first_word": first,
            **({"noise": noise} if noise else {}), **({"edges": edges} if edges else {}),
            **({"_audio": audio} if edges.get("cut_at") else {})}


def _best_take(sentence: str, language: str, profile: VoiceProfile | None, cache: Path, seed0: int,
               check: bool, style: str | None = None, tone: dict | None = None,
               context: tuple | None = None) -> tuple[np.ndarray, str, dict]:
    best = None
    seed0 += REDO.get().get(sentence, 0)
    for attempt in range((ELEVEN_TAKES if eleven_voice(profile) else MAX_TAKES) if check else 1):
        audio, engine = _take(sentence, language, profile, cache, seed0 + attempt, style, tone, context)
        if not check or engine == "macos":  # macOS `say` reads text deterministically; nothing to retry
            return audio, engine, {"tries": attempt + 1}
        try:
            score = _score(audio, sentence, language, cache)
        except Exception as e:  # noqa: BLE001 — QA must never block narration
            log.warning("voice QA unavailable: %s", e)
            return audio, engine, {"tries": attempt + 1}
        if "_audio" in score:  # made-up words after the sentence were cut off
            audio = score.pop("_audio")
        if best is None or (score["bad"], score["wer"]) < (best[2]["bad"], best[2]["wer"]):
            best = (audio, engine, {**score, "take": attempt + 1})
        if not score["bad"]:
            break
    audio, engine, info = best
    info["tries"] = attempt + 1
    if info["bad"]:
        info["flag"] = True
        log.warning("sentence still fails QA after %d takes (wer %.2f): %s", info["tries"], info["wer"], sentence)
    return audio, engine, info
