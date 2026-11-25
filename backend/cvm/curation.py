"""Automatic validation of a finished video ("Conferido pela IA"), the pipeline step "validating" between the render
and the SEO (user, 2026-11-24: "a curadoria não é por cena. É automática pela IA. Aí depois eu aprovo ou não o vídeo.
A ideia é que a IA detecte problemas antes mesmo de chegar em mim.").

It gathers what the existing checkers already measured — no new heavy model runs here, so the step needs no GPU slot:
- narration: the whisper round trip of every sentence (cvm/voice/qa.py, results in the narration Asset.meta["qa"]);
- visuals: the Gemma judge verdicts (cvm/visuals/judge.py, Asset.meta["judge"]) and clip steadiness
  (cvm/visuals/clip_rank.py, Asset.meta["steady"]);
- flags: a flag scene must show the official flag, never an AI drawing (cvm/visuals/flags.py);
- render: black/frozen/silence/loudness (cvm/render/qa.py, Render.meta["analysis"]) and the clean end screen of long
  YouTube videos (Render.meta["end_screen_from"]);
- structure: hook/promise/blocks/subscribe asks of long videos, and the Shorts rules (no subscribe ask);
- content: a light Claude check of the script against its sources, ONLY through the subscription (run_claude) and
  never blocking: at Claude's limit, or on any failure, it is skipped and the report says so.

A failing "hard" check sends the job back BY ITSELF to the earliest step that can fix it, with a note: narration →
voicing (the flagged sentences get new seeds), judge-rejected/AI-drawn flag visuals → sourcing_visuals (new seeds),
render problems → composing, structure/fact problems → scripting (pipeline.send_to_revision; the new script then goes
to the user's approval as always). At most `max_attempts` times per finished video (Setting "curation"), and the
same check is never sent back twice: what is still wrong after that is handed to the user as a warning. The report
({checks, attempts, fixed, left, ...}) is stored in every render's meta["curation"]; the routing is in Job.history
(entries with "curation").
"""

import json
import logging
import re
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from cvm import pipeline
from cvm.models import Asset, Job, Project, Render, Script, Setting

log = logging.getLogger(__name__)

KEY = "curation"
DEFAULTS = {"enabled": True, "max_attempts": 2}
ORDER = ["scripting", "voicing", "sourcing_visuals", "composing"]  # earliest first: the one that redoes the most wins
LABELS = {  # pt-BR; the UI translates by check name
    "voice": "Narração", "narration_version": "Narração do roteiro atual", "visuals": "Imagens de IA",
    "clips": "Clipes animados", "flags": "Bandeiras", "real_footage": "Material real", "render": "Render",
    "end_screen": "Tela final", "structure": "Estrutura", "shorts": "Regras de Shorts", "content": "Conteúdo e fatos",
    "editorial_credit": "Créditos do material", "editorial_proof": "Provas da matéria", "editorial_pace": "Ritmo das cenas",
    "editorial_ai": "Imagem de IA no material real", "editorial_cards": "Material real encontrado",
}
SUBSCRIBE = re.compile(r"(?i)\b(?:inscrev\w*|subscri\w*|se\s+inscreve)\b|اشترك")
LUFS_FAR = 6.0  # LU from the target: a render this far off is mixed again (2–6 LU is only a warning)
FREEZE_LONG_S = 12.0  # a still picture this long outside the end screen is a broken edit
REAL_TYPES = {"broll", "footage", "headline", "source_clip", "user_media"}


# ---------------- settings ----------------

def get(session: Session) -> dict:
    row = session.get(Setting, KEY)
    return {**DEFAULTS, **({k: v for k, v in (row.value or {}).items() if k in DEFAULTS} if row else {})}


def put(session: Session, data: dict) -> dict:
    value = get(session)
    if "enabled" in data:
        value["enabled"] = bool(data["enabled"])
    if "max_attempts" in data:
        value["max_attempts"] = max(0, min(5, int(data["max_attempts"])))
    row = session.get(Setting, KEY) or Setting(key=KEY)
    row.value = value
    session.add(row)
    session.commit()
    return value


# ---------------- history helpers ----------------

def _window(job: Job) -> list[dict]:
    """History since the user last decided on the finished video (a rejection at publish review starts over)."""
    hist = job.history or []
    last = max((i for i, h in enumerate(hist) if h.get("from") == "awaiting_publish_review"), default=-1)
    return hist[last + 1:]


def routes(job: Job) -> list[dict]:
    """The curation send-backs of this finished video, oldest first: [{"to", "checks": [names], "attempt"}]."""
    return [h["curation"] for h in _window(job) if isinstance(h.get("curation"), dict)]


def attempts(job: Job) -> int:
    return len(routes(job))


def _last_entry_to(job: Job, state: str) -> dict | None:
    return next((h for h in reversed(job.history or []) if h.get("to") == state), None)


def voice_redo(job: Job) -> dict[str, int]:
    """sentence text → seed shift, when this voicing pass was asked by curation (the flagged takes get new seeds;
    the rest come back from the take cache unchanged, so ElevenLabs credits are spent only on the redone ones)."""
    h = _last_entry_to(job, "voicing")
    c = (h or {}).get("curation") or {}
    shift = 101 * int(c.get("attempt") or 0)
    return {s: shift for s in c.get("sentences") or []} if shift else {}


def visual_shift(job: Job) -> int:
    """Seed shift for the judge's redraws when this visuals pass was asked by curation (else the same seeds would
    draw the same rejected images again)."""
    h = _last_entry_to(job, "sourcing_visuals")
    c = (h or {}).get("curation") or {}
    return 10000 * int(c.get("attempt") or 0)


# ---------------- checks ----------------

def _check(name: str, ok: bool, detail: str = "", fix_state: str | None = None, soft: bool = False, **extra) -> dict:
    return {"name": name, "label": LABELS.get(name, name), "ok": ok, "detail": detail,
            **({"fix_state": fix_state} if fix_state and not ok and not soft else {}),
            **({"soft": True} if soft and not ok else {}), **extra}


def _latest_scripts(session: Session, project: Project) -> dict[str, Script]:
    out: dict[str, Script] = {}
    for s in session.exec(select(Script).where(Script.project_id == project.id).order_by(col(Script.version).desc())):
        out.setdefault(s.language, s)
    return out


def _is_short(renders: list[Render], language: str) -> bool:
    from cvm.shorts import VERTICAL

    mine = [r for r in renders if r.language == language]
    return bool(mine) and all(r.profile in VERTICAL or r.part is not None for r in mine)


def check_voice(session: Session, project: Project, scripts: dict[str, Script]) -> list[dict]:
    out = []
    narrations = {a.language: a for a in session.exec(select(Asset).where(Asset.project_id == project.id,
                                                                           Asset.kind == "narration"))}
    flagged, unchecked, outdated, total = [], [], [], 0
    sentences: list[str] = []
    for lang in project.languages or []:
        a = narrations.get(lang)
        if not a:
            continue
        qa = (a.meta or {}).get("qa") or {}
        total += int(qa.get("sentences") or 0)
        if not qa.get("checked"):
            unchecked.append(lang)
        for f in qa.get("flagged") or []:
            flagged.append(f"{lang} cena {f.get('scene')}: “{str(f.get('text', ''))[:80]}” (ouvido: “{str(f.get('heard', ''))[:80]}”)")
            if f.get("text"):
                sentences.append(f["text"])
        sc = scripts.get(lang)
        if sc and a.meta.get("script_version") not in (None, sc.version):
            outdated.append(lang)
    if outdated:
        out.append(_check("narration_version", False, f"narração feita de uma versão antiga do roteiro ({', '.join(outdated)})",
                          "voicing"))
    if flagged:
        out.append(_check("voice", False, f"{len(flagged)} frase(s) com palavras trocadas ou cortadas: " + "; ".join(flagged[:4]),
                          "voicing", sentences=sentences))
    elif unchecked:
        out.append(_check("voice", False, f"narração não conferida pelo whisper ({', '.join(unchecked)})", soft=True))
    else:
        out.append(_check("voice", True, f"{total} frase(s) ouvidas de volta pelo whisper, nenhuma com erro"))
    return out


def check_visuals(session: Session, project: Project, scripts: dict[str, Script]) -> list[dict]:
    from cvm.visuals import clip_rank, flags
    from cvm.visuals.sources import is_ai_still

    out = []
    visuals = session.exec(select(Asset).where(Asset.project_id == project.id, Asset.kind == "visual")).all()
    main = scripts.get((project.languages or [None])[0])
    scenes = {s["index"]: s for s in (main.content.get("scenes") or [])} if main else {}

    def is_ai(a: Asset) -> bool:
        return is_ai_still(a.source) or bool((a.meta or {}).get("still"))

    ai = [a for a in visuals if is_ai(a)]
    judged = [a for a in ai if (a.meta or {}).get("judge")]
    # still failing after the judge's own redraws ("mantida (melhor tentativa)"); a clip sent back to a still that
    # passed keeps the clip's verdict with action "clipe trocado…", and stock that replaced it is no longer AI
    rejected = [a for a in judged if a.meta["judge"].get("ok") is False
                and not str(a.meta["judge"].get("action") or "").startswith(("clipe", "trocada"))]
    if rejected:
        out.append(_check("visuals", False, "o juiz de imagens reprovou a(s) cena(s) "
                          + ", ".join(f"{a.scene} ({', '.join((a.meta['judge'].get('problems') or [])[:2]) or 'nota baixa'})"
                                      for a in sorted(rejected, key=lambda a: a.scene or 0)[:6]),
                          "sourcing_visuals", scenes=[a.scene for a in rejected]))
    elif ai and not judged:
        out.append(_check("visuals", False, f"{len(ai)} imagem(ns) de IA sem conferência do juiz (modelo não instalado)", soft=True))
    elif ai:
        redone = sum(1 for a in judged if (a.meta["judge"].get("action") or "").startswith(("refeita", "trocada", "clipe")))
        out.append(_check("visuals", True, f"{len(judged)} imagem(ns) de IA conferidas pelo juiz"
                          + (f", {redone} refeita(s) ou trocada(s) na hora" if redone else "")))
    clips = [a for a in visuals if (a.meta or {}).get("kind") == "video" and a.meta.get("still")]
    steady = [a.meta["steady"] for a in clips if isinstance(a.meta.get("steady"), int | float)]
    if clips:
        shaky = [a.scene for a in clips if isinstance(a.meta.get("steady"), int | float) and a.meta["steady"] < clip_rank.STEADY]
        out.append(_check("clips", not shaky, f"clipes que perdem o assunto nas cenas {shaky}" if shaky else
                          f"{len(clips)} clipe(s) animado(s) seguram a imagem" + (f" (mínimo {min(steady):.2f})" if steady else ""),
                          soft=True))
    bad_flags = []
    for a in visuals:
        sc = scenes.get(a.scene)
        about_flag = bool(sc) and flags.scene_flag(sc)[0]
        judge_flag = any(w in p.lower() for p in ((a.meta or {}).get("judge") or {}).get("problems") or []
                         for w in ("flag", "bandeira"))
        if is_ai(a) and not (a.meta or {}).get("flag") and (about_flag or judge_flag) and project.style != "kids":
            bad_flags.append(a.scene)
    if bad_flags:
        out.append(_check("flags", False, f"bandeira desenhada por IA nas cenas {sorted(bad_flags)} (só a oficial vale)",
                          "sourcing_visuals", scenes=sorted(bad_flags)))
    elif any(sc and flags.scene_flag(sc)[0] for sc in scenes.values()):
        out.append(_check("flags", True, "bandeiras oficiais (Wikimedia Commons)"))
    if project.style != "kids":
        drawn = [a.scene for a in ai if (scenes.get(a.scene) or {}).get("visual", {}).get("type") in REAL_TYPES]
        if drawn:
            out.append(_check("real_footage", False, f"imagem de IA onde o roteiro pedia material real (cenas {sorted(drawn)[:8]}): "
                              "não achei vídeo ou foto real", soft=True))
    return out


def check_renders(renders: list[Render]) -> list[dict]:
    from cvm.render.qa import TARGET_LUFS

    out, hard, soft, missing_end = [], [], [], []
    for r in renders:
        tag = f"{r.language}/{r.profile}" + (f" parte {r.part}" if r.part else "")
        m = r.meta or {}
        if r.profile == "youtube" and r.part is None and m.get("end_screen_from") is None:
            missing_end.append(tag)
        a = m.get("analysis") or {}
        frozen = sorted(a.get("frozen") or [])
        if m.get("end_screen_s") and frozen and frozen[-1] >= float(m["end_screen_s"]) - 3:
            frozen = frozen[:-1]  # the held last frame under the end screen is still on purpose
        if frozen and frozen[-1] > FREEZE_LONG_S:
            hard.append(f"{tag}: imagem parada por {frozen[-1]:.0f}s")
        elif frozen:
            soft.append(f"{tag}: imagem parada por {frozen[-1]:.0f}s")
        if a.get("silences"):
            soft.append(f"{tag}: silêncio de {max(a['silences']):.0f}s")
        if a.get("lufs") is not None:
            off = abs(float(a["lufs"]) - TARGET_LUFS)
            (hard if off > LUFS_FAR else soft if off > 2 else []).append(f"{tag}: volume {a['lufs']:.0f} LUFS (alvo {TARGET_LUFS:.0f})")
    if missing_end:
        out.append(_check("end_screen", False, f"sem os 20 s limpos da tela final em {', '.join(missing_end)}", "composing"))
    elif any(r.profile == "youtube" and r.part is None for r in renders):
        out.append(_check("end_screen", True, "20 s finais limpos para a tela final"))
    if hard:
        out.append(_check("render", False, "; ".join(hard + soft), "composing"))
    elif soft:
        out.append(_check("render", False, "; ".join(soft), soft=True))
    else:
        out.append(_check("render", True, f"{len(renders)} vídeo(s) sem tela preta, imagem parada, silêncio ou volume fora"))
    return out


def check_structure(project: Project, scripts: dict[str, Script], renders: list[Render]) -> list[dict]:
    out = []
    short_bad, long_warn, shorts, longs = [], [], 0, 0
    for lang, sc in scripts.items():
        if lang not in (project.languages or []):
            continue
        c = sc.content or {}
        if _is_short(renders, lang):
            shorts += 1
            ask = [s["index"] for s in c.get("scenes") or [] if SUBSCRIBE.search(s.get("narration") or "")]
            if c.get("cta_points") or ask:
                short_bad.append(f"{lang}: pedido de inscrição num Short" + (f" (cena {ask[0]})" if ask else ""))
        elif c.get("blocks"):
            longs += 1
            miss = [n for n, ok in (("gancho", c.get("hook")), ("promessa", c.get("promise")),
                                    ("fim da introdução", c.get("intro_end") is not None)) if not ok]
            if project.style != "kids":
                at = {p.get("at") for p in c.get("cta_points") or [] if isinstance(p, dict)}
                miss += [n for n, k in (("inscrição no meio", "mid"), ("inscrição no fim", "end")) if k not in at]
            if miss:
                long_warn.append(f"{lang}: falta {', '.join(miss)}")
    if short_bad:
        out.append(_check("shorts", False, "; ".join(short_bad) + ". Short não pede inscrição e termina em loop.", "scripting"))
    elif shorts:
        out.append(_check("shorts", True, "sem pedido de inscrição"))
    if long_warn:
        out.append(_check("structure", False, "; ".join(long_warn), soft=True))
    elif longs:
        out.append(_check("structure", True, "gancho, promessa, vinheta, blocos e pedidos de inscrição no lugar"))
    return out


# ---------------- content (Claude, subscription only, never blocking) ----------------

CONTENT_PROMPT = """Você confere o roteiro de um vídeo já pronto antes de ele ir para o dono do canal. Responda SÓ com JSON.

Tema: {topic}
Estilo: {style}{short}
Fontes citadas pelo roteirista: {sources}

Roteiro ({language}), cena por cena:
{scenes}

Procure apenas problemas reais e graves: um fato errado ou inventado (data, número, nome, lugar), algo que contradiz as
fontes, uma promessa do gancho que o vídeo não cumpre, frases que se contradizem.{short_rule} Não comente estilo, gosto ou
ritmo. Se puder, confira um fato duvidoso nas fontes com WebFetch/WebSearch.

Formato: {{"ok": true|false, "problems": [{{"scene": <índice ou null>, "issue": "<o problema, em português>",
"severity": "high"|"low", "fix": "<como corrigir, em uma frase>"}}]}}
"high" = erro de fato ou promessa não cumprida (o vídeo não pode sair assim); "low" = vale avisar.
"""
ALLOWED = ["WebFetch", "WebSearch"]
DENY = ["mcp__cvm__*", "Agent", "Bash", "Write", "Edit", "NotebookEdit", "Skill"]


def _json_object(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text or "")
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        data = json.loads(text[a:b + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _content_key(project_id: int) -> str:
    return f"curation_content:{project_id}"


def check_content(session: Session, project: Project, scripts: dict[str, Script], renders: list[Render],
                  claude_slot=None) -> dict:
    """The light Claude check of the main script. Cached per script version (a voicing/visuals retry doesn't ask
    again). Skipped — never blocking — when Claude is off, at its limit, busy or failing."""
    from cvm import claude_limit, engines

    lang = (project.languages or [None])[0]
    sc = scripts.get(lang)
    if not sc:
        return _check("content", False, "sem roteiro para conferir", soft=True, skipped=True)
    ids = sorted(s.id for s in scripts.values())
    row = session.get(Setting, _content_key(project.id))
    if row and row.value.get("scripts") == ids and row.value.get("check"):
        return row.value["check"]
    if not engines.auto_run():
        return _check("content", False, "conferência dos fatos pulada: Claude desligado em Configurações", soft=True, skipped=True)
    from cvm.scripting import agents

    # Claude at its limit: Codex takes the check when it can (agents.run_light); skipped only when nobody can
    if until := agents.wait_until(session, "curation", project):
        return _check("content", False, f"conferência dos fatos pulada: {claude_limit.label(until)}", soft=True, skipped=True)
    c = sc.content or {}
    short = _is_short(renders, lang)
    prompt = CONTENT_PROMPT.format(
        topic=(project.title or project.topic or "")[:300], style=project.style or "-",
        short=" (Short vertical)" if short else "", sources=", ".join(c.get("sources") or []) or "(nenhuma)",
        language=lang, scenes="\n".join(f"{s.get('index')}. {s.get('narration', '')}" for s in c.get("scenes") or [])[:20000],
        short_rule=" Num Short: o final deve fechar em loop com o começo." if short else "")
    try:  # the Claude slot is held only on the Claude path (run_light): a Codex check never occupies it
        res = agents.run_light(prompt, "curation", timeout=900, mcp="empty", allowed_tools=ALLOWED,
                               disallowed_tools=DENY, claude_slot=claude_slot)
    except agents.ClaudeBusy:
        return _check("content", False, "conferência dos fatos pulada: Claude ocupado com outras etapas", soft=True, skipped=True)
    except claude_limit.ClaudeLimit as e:
        return _check("content", False, f"conferência dos fatos pulada: {claude_limit.label(e.until)}", soft=True, skipped=True)
    except Exception as e:  # noqa: BLE001 — never block the queue on this check
        log.warning("curation content check of %s failed: %s", project.id, e)
        return _check("content", False, "conferência dos fatos pulada: o Claude não respondeu", soft=True, skipped=True)
    data = _json_object(str(res.get("result", "")))
    if "ok" not in data:
        return _check("content", False, "conferência dos fatos pulada: resposta ilegível", soft=True, skipped=True)
    probs = [p for p in data.get("problems") or [] if isinstance(p, dict) and p.get("issue")]
    high = [p for p in probs if p.get("severity") == "high"]
    fmt = lambda p: (f"cena {p['scene']}: " if p.get("scene") is not None else "") + str(p["issue"])[:240] \
        + (f" → {str(p['fix'])[:160]}" if p.get("fix") else "")  # noqa: E731
    if high:
        check = _check("content", False, "; ".join(fmt(p) for p in high[:5]), "scripting", problems=probs[:8])
    elif probs:
        check = _check("content", False, "; ".join(fmt(p) for p in probs[:5]), soft=True, problems=probs[:8])
    else:
        check = _check("content", True, "roteiro conferido com as fontes, sem erro de fato")
    row = row or Setting(key=_content_key(project.id))
    row.value = {"scripts": ids, "check": check}
    session.add(row)
    session.commit()
    return check


EDITORIAL_MEDIAN_S = 4.5
EDITORIAL_MAX_S = 8.0
EDITORIAL_MAX_READ_S = 15.0  # lista and prova hold reading time
EDITORIAL_CARDS_MAX = 0.30


def check_editorial(session: Session, project: Project, scripts: dict[str, Script], renders: list[Render]) -> list[dict]:
    """News-desk long videos: reads each language's plan.json (written by `visuals.editorial.source`)."""
    from cvm import editorial
    from cvm.paths import project_dir
    from cvm.visuals.sources import is_ai_still

    out: list[dict] = []
    for lang in editorial.long_languages(session, project):
        path = project_dir(project.id, lang) / "editorial" / "plan.json"
        if not path.exists():
            continue
        scenes = [s for s in json.loads(path.read_text()) if isinstance(s, dict)]
        tag = f" ({lang})" if len(project.languages or []) > 1 else ""
        body = [s for s in scenes if not (s.get("end_screen") or s.get("fim") or s.get("tipo") in ("fim", "vinheta"))]
        media = [s for s in body if s.get("tipo_pedido", s.get("tipo")) in ("video", "foto")]
        real = [s for s in body if s.get("tipo") in ("video", "foto") and s.get("media")]

        nocredit = [i for i, s in enumerate(real, 1)
                    if not (s["media"].get("own") or s["media"].get("credit") or s.get("fonte"))]
        out.append(_check("editorial_credit", not nocredit,
                          f"cenas {nocredit[:8]} usam material de terceiros sem crédito{tag}" if nocredit
                          else f"todo material de terceiros tem crédito{tag}", "sourcing_visuals", scenes=nocredit))

        proofs = [(i, s) for i, s in enumerate(body, 1) if s.get("tipo") == "prova" and not s.get("fallback")]
        noboxes = [i for i, s in proofs if not s.get("boxes")]
        if proofs:
            out.append(_check("editorial_proof", not noboxes,
                              f"prints sem o trecho marcado nas cenas {noboxes[:8]}{tag}" if noboxes
                              else f"{len(proofs)} print(s) com o trecho marcado{tag}", "sourcing_visuals", scenes=noboxes))

        lens = sorted(float(s["end"]) - float(s["start"]) for s in body if "start" in s and "end" in s)
        if lens:
            n = len(lens)
            median = lens[n // 2] if n % 2 else (lens[n // 2 - 1] + lens[n // 2]) / 2
            def cap(s: dict) -> float:
                return EDITORIAL_MAX_READ_S if s.get("tipo") in ("lista", "prova") else EDITORIAL_MAX_S

            long_ = [i for i, s in enumerate(body, 1)
                     if "start" in s and float(s["end"]) - float(s["start"]) > cap(s) + 0.05]  # frame rounding
            ok = median <= EDITORIAL_MEDIAN_S and not long_
            detail = (f"cena mediana de {median:.1f} s (máximo {EDITORIAL_MEDIAN_S:g} s)" if median > EDITORIAL_MEDIAN_S else "")
            if long_:
                detail += ("; " if detail else "") + f"cenas {long_[:8]} passam do limite ({EDITORIAL_MAX_S:g} s, ou {EDITORIAL_MAX_READ_S:g} s em lista e print)"
            out.append(_check("editorial_pace", ok, (detail + tag) if not ok else f"cena mediana de {median:.1f} s{tag}",
                              "scripting", scenes=long_))

        ai = [i for i, s in enumerate(real, 1)
              if is_ai_still(s["media"].get("source")) or is_ai_still(str(s["media"].get("file") or "").rsplit("/", 1)[-1])
              or "/ai/" in str(s["media"].get("file") or "")]
        out.append(_check("editorial_ai", not ai,
                          f"imagem de IA em cena de vídeo ou foto ({ai[:8]}){tag}" if ai else f"nenhuma imagem de IA no material real{tag}",
                          "sourcing_visuals", scenes=ai))

        if media:
            cards = [s for s in media if s.get("fallback")]
            share = len(cards) / len(media)
            out.append(_check("editorial_cards", share <= EDITORIAL_CARDS_MAX,
                              f"pouco material real encontrado: {len(cards)} de {len(media)} cenas viraram cartão{tag}"
                              if share > EDITORIAL_CARDS_MAX else f"{len(media) - len(cards)} de {len(media)} cenas com material real{tag}",
                              soft=True))
    return out


# ---------------- the step ----------------

def _editorial_only(session: Session, project: Project) -> bool:
    """Every render is a news-desk long video: the classic visual set is not used, so old `visual` assets left from
    an earlier classic render must not be judged (check_editorial reads plan.json instead)."""
    from cvm import editorial
    from cvm.visuals.editorial import needs_classic

    return bool(editorial.long_languages(session, project)) and not needs_classic(session, project)


def run_checks(session: Session, project: Project, claude_slot=None) -> list[dict]:
    renders = session.exec(select(Render).where(Render.project_id == project.id)).all()
    scripts = _latest_scripts(session, project)
    checks: list[dict] = []
    for fn in (lambda: check_voice(session, project, scripts),
               lambda: [] if _editorial_only(session, project) else check_visuals(session, project, scripts),
               lambda: check_renders(renders), lambda: check_structure(project, scripts, renders),
               lambda: check_editorial(session, project, scripts, renders)):
        try:
            checks += fn()
        except Exception as e:  # noqa: BLE001 — a broken checker is reported, never blocks the video
            log.exception("curation checker failed on project %s", project.id)
            checks.append(_check("internal", False, f"um conferidor falhou: {e}"[:300], soft=True))
    checks.append(check_content(session, project, scripts, renders, claude_slot))
    return checks


def _summary(report: dict) -> str:
    ok = sum(1 for c in report["checks"] if c["ok"])
    warn = sum(1 for c in report["checks"] if not c["ok"])
    parts = [f"{ok} ok"]
    if report["fixed"]:
        parts.append(f"{len(report['fixed'])} corrigido(s)")
    if warn:
        parts.append(f"{warn} aviso(s)")
    return "Conferido pela IA: " + ", ".join(parts)


def validate(job: Job, session: Session, claude_slot=None) -> str | None:
    """The "validating" handler: hand the video on to "seo" with the report, or send it back to the step that fixes
    the earliest hard problem (the job is moved and committed here; returns None then)."""
    cfg = get(session)
    if not cfg["enabled"]:
        return "seo"
    project = session.get(Project, job.project_id)
    checks = run_checks(session, project, claude_slot)
    past = routes(job)
    tried = {n for r in past for n in r.get("checks") or []}
    fixed = []
    for name in sorted(tried):
        if any(c["name"] == name and c["ok"] for c in checks) or not any(c["name"] == name for c in checks):
            n = sum(1 for r in past if name in (r.get("checks") or []))
            fixed.append({"name": name, "label": LABELS.get(name, name), "attempts": n,
                          "detail": next((r.get("details", {}).get(name, "") for r in reversed(past)
                                          if name in (r.get("checks") or [])), "")})
    hard = [c for c in checks if not c["ok"] and c.get("fix_state") and c["name"] not in tried]
    again = [c for c in checks if not c["ok"] and c.get("fix_state") and c["name"] in tried]
    for c in again:  # tried once already and still wrong: the user decides
        c["detail"] = f"{c['detail']} (tentei corrigir, não resolveu)"
    capped = bool(hard) and len(past) >= cfg["max_attempts"]
    report = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "attempts": len(past),
              "max_attempts": cfg["max_attempts"], "checks": checks, "fixed": fixed,
              "left": [c["name"] for c in checks if not c["ok"] and (c.get("fix_state") and (c in again or capped))],
              "capped": capped}
    if hard and not capped:
        dst = min((c["fix_state"] for c in hard), key=ORDER.index)
        send = [c for c in hard if c["fix_state"] == dst] + [c for c in hard if c["fix_state"] != dst]
        report["routed_to"] = dst
        _store(session, project, report)
        note = "Conferência automática: " + "; ".join(f"{c['label']}: {c['detail']}" for c in send)[:1500]
        entry = {"to": dst, "attempt": len(past) + 1, "checks": [c["name"] for c in send],
                 "details": {c["name"]: c["detail"][:300] for c in send},
                 **({"sentences": [s for c in send for s in c.get("sentences") or []]} if dst == "voicing" else {})}
        if dst == "scripting":
            pipeline.send_to_revision(job, note + " Corrija só isso e mantenha o resto do roteiro.")
        else:
            pipeline.advance(job, dst, note)
        job.history = [*job.history[:-1], {**job.history[-1], "curation": entry}]
        session.add(job)
        session.commit()
        log.info("job %s: curation sends it back to %s (%s)", job.id, dst, entry["checks"])
        return None
    _store(session, project, report)
    pipeline.advance(job, "seo", _summary(report))
    counts = {"ok": sum(1 for c in report["checks"] if c["ok"]), "fixed": len(fixed),
              "warnings": sum(1 for c in report["checks"] if not c["ok"])}  # for the "Sua vez" cards
    job.history = [*job.history[:-1], {**job.history[-1], "curation_summary": counts}]
    session.add(job)
    session.commit()
    return None


def _store(session: Session, project: Project, report: dict) -> None:
    for r in session.exec(select(Render).where(Render.project_id == project.id)).all():
        r.meta = {**(r.meta or {}), "curation": report}
        session.add(r)
    session.commit()


def latest_report(session: Session, project_id: int) -> dict | None:
    """The newest report among the project's renders (they all carry the same one)."""
    reports = [r.meta["curation"] for r in session.exec(select(Render).where(Render.project_id == project_id)).all()
               if isinstance((r.meta or {}).get("curation"), dict)]
    return max(reports, key=lambda x: x.get("at", ""), default=None)
