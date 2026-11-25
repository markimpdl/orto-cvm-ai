"""`cvm` MCP server: lets Claude Code agents read and write the app's data.

Publishing tools are intentionally absent here; the publisher agent can only schedule,
and a PreToolUse hook blocks scheduling for jobs that were not approved by the user.
"""

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from sqlmodel import Session, col, select

from cvm import pipeline
from cvm.db import engine, init_db
from cvm import private as _private
from cvm.channels import brief_text, episode_number, for_agents, producing, series_episodes, series_of
from cvm.models import Asset, Avatar, Channel, Job, Project, Render, Script, Trend, TrendAnalysis, VoiceProfile
from cvm.scripting.schema import ScriptContent

mcp = FastMCP("cvm")


@mcp.tool()
def list_trends(region: str | None = None, limit: int = 30) -> list[dict]:
    """Top trends by viral score. region: BR | AE | US."""
    with Session(engine) as s:
        from cvm.trends import current

        q = current(s, select(Trend)).order_by(col(Trend.score).desc()).limit(limit)
        if region:
            q = q.where(Trend.region == region)
        return [t.model_dump(mode="json") for t in s.exec(q).all()]


MAX_PREVIEWS = 16


def _private_style_rules(project, editorial: bool) -> str:
    style = str(getattr(project.style, "value", project.style))
    return "\n\n".join(t for t in (_private.skill_rules(f"script-style-{style}"),
                                    _private.skill_rules("script-style-editorial") if editorial else "") if t)


@mcp.tool()
def get_project(project_id: int, detail: str = "full"):
    """Project with its trend, channel, the LATEST script per language (older versions only listed in
    `older_scripts`) and job state. detail="text" leaves out each scene's visual details (prompts, search terms,
    motion) and keeps index, speaker, narration, on-screen text, timing and visual type: use it when you don't
    need the shot list (SEO, packaging). When the user attached their own files (user_media), the result also
    carries one preview image per file, in the same order as `user_media` (an image, or a 2x2 grid of 4 frames of
    a video) — look at them before writing."""
    data = _project_data(project_id, text_only=detail == "text")
    previews = [m["preview"] for m in data["user_media"] if m.get("preview") and Path(m["preview"]).exists()][:MAX_PREVIEWS]
    if not previews:
        return data
    from mcp.server.fastmcp import Image

    return [data, *(Image(path=p) for p in previews)]


TEXT_DROP = ("cast_looks", "voice_style", "pronunciation")  # drawing/voicing only


def _text_scenes(content: dict) -> dict:
    """A script for writers of titles/SEO: each scene as one line "[index] speaker: narration | tela: text" (kids
    episodes have 150+ scenes; as objects they made get_project too big for a subagent to read)."""
    def line(sc: dict) -> str:
        who = f"{sc['speaker']}: " if sc.get("speaker") else ""
        screen = f" | tela: {sc['on_screen_text']}" if sc.get("on_screen_text") else ""
        return f"[{sc.get('index')}] {who}{sc.get('narration') or ''}{screen}"

    return {**{k: v for k, v in content.items() if k not in TEXT_DROP},
            "scenes": [line(sc) for sc in content.get("scenes") or []]}


def _latest_scripts(scripts: list[Script], text_only: bool) -> tuple[list[dict], list[dict]]:
    """Newest version per language in full (kids episodes run ~100 KB a version, and every old version made
    get_project too big for the subagents to read); the older ones only as {id, language, version}."""
    latest, older, seen = [], [], set()
    for x in scripts:  # newest first
        if x.language in seen:
            older.append({"id": x.id, "language": x.language, "version": x.version})
            continue
        seen.add(x.language)
        d = x.model_dump(mode="json")
        if text_only:
            d["content"] = _text_scenes(d.get("content") or {})
        latest.append(d)
    return latest, older


def _project_data(project_id: int, text_only: bool = False) -> dict:
    from cvm import editorial as _editorial
    from cvm import style_rules as _style_rules
    from cvm.render import bumper as _bumper

    with Session(engine) as s:
        p = s.get(Project, project_id)
        if not p:
            raise ValueError(f"project {project_id} not found")
        trend = s.get(Trend, p.trend_id) if p.trend_id else None
        channel = s.get(Channel, p.channel_id) if p.channel_id else None
        scripts = s.exec(select(Script).where(Script.project_id == project_id).order_by(col(Script.version).desc())).all()
        job = s.exec(select(Job).where(Job.project_id == project_id)).first()
        renders = s.exec(select(Render).where(Render.project_id == project_id)).all()
        avatar = s.get(Avatar, p.avatar_id) if p.avatar_id else None
        voices = s.exec(select(VoiceProfile).where(VoiceProfile.avatar_id == p.avatar_id)).all() if avatar else []
        # earlier videos of the same channel — or of the series, on every channel/language — oldest first:
        # series continuity, and no repeated topics
        series = series_of(s, p)
        earlier = series_episodes(s, series.id, p.id) if series else []
        if series and (mine := episode_number(p.title)):  # a numbered episode: earlier ones by number, not by id
            eps = [e for e in series_episodes(s, series.id) if e.id != p.id]
            earlier = ([e for e in eps if not episode_number(e.title) and e.id < p.id]
                       + sorted((e for e in eps if 0 < (episode_number(e.title) or 0) < mine),
                                key=lambda e: episode_number(e.title)))
        # how the last episode ended (its teaser is this episode's promise): same language first
        same_lang = [e for e in earlier if (e.languages or [None])[0] == (p.languages or [None])[0]]
        previous = None
        for e in reversed(same_lang or earlier):
            sc = s.exec(select(Script).where(Script.project_id == e.id).order_by(col(Script.id).desc())).first()
            if sc and sc.content.get("scenes"):
                previous = {"title": e.title, "ending": [
                    f"{x.get('speaker') or 'Narrador'}: {x.get('narration', '')}" for x in sc.content["scenes"][-8:]]}
                break
        titles = [e.title for e in earlier]
        # episodes made on other machines while the online cluster existed (removed 2026-11-24; the SharedEpisode
        # rows already saved are only read, nothing writes them now): their titles, and their ending when one of
        # them is the latest episode in this language
        if series and series.uid:
            from cvm.models import SharedEpisode

            lang = (p.languages or [None])[0]
            mine = episode_number(p.title)

            def before(x) -> bool:  # earlier than this episode: by number, else by date
                if mine and x.number:
                    return x.number < mine
                return not (x.made_at and p.created_at) or x.made_at.replace(tzinfo=None) < p.created_at.replace(tzinfo=None)

            shared = [x for x in s.exec(select(SharedEpisode).where(SharedEpisode.series_uid == series.uid)).all()
                      if before(x)]
            titles = sorted(set(titles) | {x.title for x in shared}, key=lambda t: (episode_number(t) or 0, t))
            local_last = max((episode_number(e.title) or 0 for e in same_lang), default=0)
            remote = sorted((x for x in shared if x.language == lang and x.ending), key=lambda x: x.number or 0)
            if remote and (remote[-1].number or 0) > local_last:
                previous = {"title": remote[-1].title, "ending": remote[-1].ending, "machine": remote[-1].machine}
        latest, older = _latest_scripts(scripts, text_only)
        return {
            # whether this video opens with hook + promise and plays the channel bumper (the user's choice per video,
            # else the channel's default): hook false = start straight on the content with a plain opening line
            "opening": {"hook": _bumper.hook_on(p, channel), "bumper": _bumper.bumper_on(p, channel)},
            # the user's rules for this video type (Configurações > Regras por tipo): apply on top of the style skill
            "style_rules": _style_rules.for_style(s, p.style),
            "channel_previous_titles": titles[-40:],
            "channel_previous_episode": previous,
            # the user's own clips/images for this video: place them with visual {type: "user_media", media_id}
            "user_media": [{"media_id": m.id, "kind": m.meta.get("kind"), "description": m.meta.get("description"),
                            "filename": m.meta.get("filename"), "duration_s": m.meta.get("duration"),
                            "preview": m.meta.get("preview"),
                            # videos: has_audio = the clip has sound; keep_audio = the user wants that sound kept
                            # (the scene plays the recording, no narration over it)
                            **({"has_audio": bool(m.meta.get("has_audio")), "keep_audio": bool(m.meta.get("keep_audio"))}
                               if m.meta.get("kind") == "video" else {}),
                            # media_cut "highlights": the only ranges of this video to use ([] = leave it out) and,
                            # for long recordings, the strongest candidate moments (start, end, score)
                            **(_media_ranges(m.meta, p.media_cut) if m.meta.get("kind") == "video" else {})}
                           for m in s.exec(select(Asset).where(Asset.project_id == project_id, Asset.kind == "user_media")).all()],
            # avatar_full/avatar_pip scenes only render when the avatar has a base video ("ready");
            # without one they fall back to the scene's image_prompt/search_terms
            "avatar": {"name": avatar.name, "ready": avatar.status == "ready",
                       "voice_languages": [v.language for v in voices if v.ref_audio_path]} if avatar else None,
            # "podcast": the avatar hosts from a podcast desk — a small window the whole video, full screen in avatar_full
            "presenter": p.presenter or (channel.presenter if channel else None) or "scenes",
            # news-desk long video (skill script-style-editorial): the script must carry `editorial.cenas` for these
            # languages; Shorts and ar-AE keep the current editorial path
            "editorial_long": bool(ed_langs := _editorial.long_languages(s, p)),
            "editorial_languages": ed_langs,
            # owner-only rules (private/): apply on top of the style skill and the channel brief; "" when none
            "private_rules": {"style": _private_style_rules(p, bool(ed_langs)),
                              "channel": _private.channel_rules(channel) if channel else ""},
            "renders": [r.model_dump(mode="json") for r in renders],
            "project": p.model_dump(mode="json"),
            "trend": trend.model_dump(mode="json") if trend else None,
            "channel": _lean_channel(for_agents(s, p, channel)) if channel and text_only else
                       for_agents(s, p, channel) if channel else None,
            "scripts": latest,
            "older_scripts": older,
            "job": ({"state": job.state, "error": (job.error or None) and job.error[:300]} if text_only else
                    job.model_dump(mode="json")) if job else None,
        }


def _lean_channel(ch: dict) -> dict:
    """detail="text": the series' characters by name only (their looks and voices are for drawing/voicing)."""
    chars = ch.get("characters")
    return {**ch, "characters": sorted(chars)} if isinstance(chars, dict) else ch


@mcp.tool()
def save_packaging(project_id: int, language: str, packaging: dict) -> dict:
    """Set the title/thumbnail packaging of the latest script of one language WITHOUT a new version (for videos
    already produced: nothing is re-narrated). packaging = {title, thumbnail_text (≤ 3 words, channel language, not
    repeating the title), thumbnail_prompt? (never a flag), thumbnail_side?,
    thumbnail_scene?, alternatives?: [{title, thumbnail_text}]}."""
    from cvm.scripting.schema import Packaging

    Packaging.model_validate(packaging)
    with Session(engine) as s:
        last = s.exec(select(Script).where(Script.project_id == project_id, Script.language == language)
                      .order_by(col(Script.version).desc())).first()
        if not last:
            return {"error": f"no script for {language}"}
        last.content = {**last.content, "packaging": packaging}
        s.add(last)
        pipeline.name_episode(s, project_id, last.content)
        s.commit()
        return {"script_id": last.id, "version": last.version}


def _media_ranges(meta: dict, media_cut: str) -> dict:
    """What get_project shows of a video's best moments (media_cut "highlights"), or of the from–to ranges the user
    set by hand (segments_auto False: only those stretches play, whatever the project's media_cut)."""
    if media_cut != "highlights" and meta.get("segments_auto") is not False:
        return {}
    out = {"segments": meta.get("segments", [])}
    if meta.get("segments_auto") is False:
        out["by_hand"] = True
    if meta.get("moments"):
        out["moments"] = meta["moments"][:20]
    return out


def _check_editorial(s: Session, project_id: int, language: str, content: dict) -> dict:
    """Editorial long videos need the news-desk scene list and it must follow its contract (both Claude and the
    Codex fallback save through save_script, so this covers both). Elsewhere (an ar-AE copy, a Short, another
    presenter) an `editorial` list is dropped before ScriptContent validation, never a reason to refuse. Returns the
    content to save."""
    from cvm import editorial
    from cvm.scripting.schema import editorial_problems

    p = s.get(Project, project_id)
    if not (p and editorial.long_for(s, p, language)):
        content = {k: v for k, v in content.items() if k != "editorial"}  # dropped before validation: a stray
        ScriptContent.model_validate(content)                              # invalid block is no reason to refuse
        return content
    ScriptContent.model_validate(content)
    if not content.get("editorial"):
        raise ValueError("Vídeo longo editorial: falta `editorial.cenas`, a lista de cenas presas a palavras da "
                         "narração. As regras estão na skill script-style-editorial.")
    if problems := editorial_problems(content, language):
        raise ValueError("Problemas em editorial.cenas. Corrija e salve de novo:\n- " + "\n- ".join(problems))
    return content


@mcp.tool()
def save_script(project_id: int, language: str, content: dict) -> dict:
    """Save a script version. content must match ScriptContent: {hook, scenes:[{index, narration,
    on_screen_text?, visual:{type, search_terms, image_prompt?, source_url?, clip_query?, official_channels?,
    notes?, chart?: {kind: bar|timeline|stat, title?, unit?, items: [{label, value?, text?, highlight?}], source?},
    media_id?, media_start? (user_media: one of get_project's user_media, and the second where the scene cuts in),
    flag? (ISO-2 code, e.g. "AE": the scene shows that country's official flag; never put a flag in image_prompt)},
    target_seconds?, speaker? (kids: the character saying the line), delivery?}], call_to_action?, target_duration_s,
    sources[], hook_variants[]?, cast{name: English voice description}? (kids),
    pronunciation{written: spoken}?, packaging?: {title, thumbnail_text (≤ 3 words, not repeating the title),
    thumbnail_prompt? (never a flag), thumbnail_side?:
    left|right, thumbnail_scene? (scene index whose image is the background), alternatives?: [{title,
    thumbnail_text}]}, intro_end? (long videos: index of the last scene of the promise; the short bumper plays after
    it), hook_type? (question|number|contrarian|best_moment), promise?, blocks?: [{title, scenes: [indices], question,
    reveal, continuity}] (long videos: 60–90 s blocks, scenes ≤ 45 s), cta_points?: [{scene, at: mid|end, text}],
    ending?: {answer, comment_question, next_video}, editorial?: {cenas: [...]} (required when get_project says
    editorial_long for this language; contract in the skill script-style-editorial)}. visual.type in
    avatar_full|avatar_pip|broll|footage|headline|chart|source_clip|ai_image|user_media."""
    with Session(engine) as s:
        content = _check_editorial(s, project_id, language, content)  # validates ScriptContent too
        last = s.exec(
            select(Script)
            .where(Script.project_id == project_id, Script.language == language)
            .order_by(col(Script.version).desc())
        ).first()
        script = Script(project_id=project_id, language=language, content=content, version=(last.version + 1) if last else 1)
        s.add(script)
        pipeline.name_episode(s, project_id, content)
        s.commit()
        s.refresh(script)
        return {"script_id": script.id, "version": script.version}


@mcp.tool()
def save_seo(render_id: int, title: str, description: str, tags: list[str], hashtags: list[str]) -> dict:
    """Store platform-optimized metadata for a render."""
    with Session(engine) as s:
        r = s.get(Render, render_id)
        if not r:
            raise ValueError(f"render {render_id} not found")
        p = s.get(Project, r.project_id)
        if p and p.style == "kids":  # the episode number is the project's (see pipeline.fix_episode_number)
            n = episode_number(p.title)
            title = pipeline.fix_episode_number(title, n, p.title.split(" – ")[0] if n else "")
        r.seo = {"title": title, "description": description, "tags": tags, "hashtags": hashtags}
        s.add(r)
        s.commit()
        return {"ok": True}


@mcp.tool()
def performance_summary() -> list[dict]:
    """Average views of our published videos by style/platform/language (what has been working)."""
    from cvm.publish.metrics import performance_summary as summary

    with Session(engine) as s:
        return summary(s)


@mcp.tool()
def list_channels() -> list[dict]:
    """Our channels (platform, language, default style, brief) — to fit ideas to each channel."""
    with Session(engine) as s:
        return [{**c.model_dump(mode="json", include={"id", "name", "platform", "language", "default_style", "made_for_kids",
                                                      "group"}), "brief": brief_text(c)} for c in producing(s)]


IDEA_KEYS = {"trend_ids", "topic", "why", "style", "hooks", "risks", "channel_id", "potential"}


@mcp.tool()
def save_trend_analysis(analysis_id: int, summary: str, items: list[dict]) -> dict:
    """Save the trend-analyst result. items (best first, max 10): {trend_ids: [int] (may be [] for a channel's
    niche ideas), topic, why, style: dark|react|
    explainer|top_list|kids, hooks: {"pt-BR": "...", "en-US": "...", "ar-AE": "..."}, risks, channel_id?: int,
    potential: 0-100}."""
    from cvm.models import Style

    clean = []
    with Session(engine) as s:
        a = s.get(TrendAnalysis, analysis_id)
        if not a:
            raise ValueError(f"analysis {analysis_id} not found")
        for it in items[:10]:
            # trend ideas must cite trends; a channel's niche ideas may stand alone
            if not it.get("topic") or (not it.get("trend_ids") and not a.channel_id):
                raise ValueError("each item needs topic and trend_ids")
            if it.get("style") not in {s.value for s in Style}:
                raise ValueError(f"invalid style {it.get('style')}")
            clean.append({k: v for k, v in it.items() if k in IDEA_KEYS}
                         | ({"channel_id": a.channel_id, "trend_ids": it.get("trend_ids") or []} if a.channel_id else {}))
        a.summary, a.items, a.status = summary, clean, "done"
        s.add(a)
        s.commit()
        return {"ok": True, "items": len(clean)}


AGENT_ALLOWED_TARGETS = {"awaiting_approval", "awaiting_publish_review", "failed"}


@mcp.tool()
def set_job_state(job_id: int, state: str, note: str | None = None) -> dict:
    """Move a job forward after finishing an agent step. Agents may only hand work back to the
    human (awaiting_approval / awaiting_publish_review) or mark failure; approvals are UI-only."""
    if state not in AGENT_ALLOWED_TARGETS:
        raise ValueError(f"agents cannot move jobs to {state}; that is a human decision")
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if not job:
            raise ValueError(f"job {job_id} not found")
        pipeline.advance(job, state, note)
        s.add(job)
        s.commit()
        return {"job_id": job_id, "state": job.state}


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()
