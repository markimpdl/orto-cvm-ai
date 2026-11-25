"""Engines chosen in Configurações (Setting "engines"), read at run time so a change needs no restart.

.env (cvm.config.settings) gives the defaults; the Setting only overrides what the user changed:

- writer: who writes scripts/SEO/ideas. "claude" = Claude (Codex as the reserve while Claude is at its limit, when
  `codex_fallback` is on), "codex" = Codex first wherever it may write (cvm/scripting/agents.codex_allowed; kids and
  Arabic scripts still go to Claude), "manual" = nothing runs on its own (the "Rodar no Claude" button).
- codex_fallback: Codex takes over while Claude is limited.
- video: image-to-video engine per purpose ("kids" episodes, "realistic" stills, "cartoon" drawn stills): "ltx"|"wan".
  A missing LTX still falls back to Wan at the call site.
- image_engine: AI stills outside kids series ("z-image" | "flux").

The worker is another process: it sees a change within CACHE_S.
"""

import sys
import time

from cvm.config import settings

KEY = "engines"
WRITERS = ("claude", "codex", "manual")
VIDEO_PURPOSES = ("kids", "realistic", "cartoon")
VIDEO_ENGINES = ("ltx", "wan")
IMAGE_ENGINES = ("z-image", "flux")
CACHE_S = 5.0

_cache: dict = {"at": 0.0, "value": None}


def defaults() -> dict:
    return {
        "writer": "claude" if settings.claude_auto_run else "manual",
        "codex_fallback": bool(settings.codex_fallback),
        "video": {p: settings.video_engine for p in VIDEO_PURPOSES},
        "image_engine": settings.image_engine,
    }


def _stored() -> dict:
    """The saved Setting (empty when none, or when the DB can't be read)."""
    from sqlmodel import Session

    from cvm import db
    from cvm.models import Setting

    try:
        with Session(db.engine) as s:
            row = s.get(Setting, KEY)
            return dict(row.value) if row and row.value else {}
    except Exception:  # noqa: BLE001  (no table yet, locked DB: keep the .env behaviour)
        return {}


def invalidate() -> None:
    _cache.update(at=0.0, value=None)


def _saved() -> dict:
    """The Setting, re-read at most every CACHE_S."""
    if _cache["value"] is None or time.monotonic() - _cache["at"] >= CACHE_S:
        _cache.update(at=time.monotonic(), value=_stored())
    return _cache["value"]


def get() -> dict:
    """.env defaults with the user's choices on top."""
    base, saved = defaults(), _saved()
    value = {**base, **{k: v for k, v in saved.items() if k in base and k != "video"}}
    value["video"] = {**base["video"], **{k: v for k, v in (saved.get("video") or {}).items() if k in VIDEO_PURPOSES}}
    return value


def writer() -> str:
    w = get()["writer"]
    return w if w in WRITERS else "claude"


def auto_run() -> bool:
    """Agent steps (scripting, SEO) start on their own (was settings.claude_auto_run)."""
    return writer() != "manual"


def codex_fallback() -> bool:
    return bool(get()["codex_fallback"])


def video_engine(purpose: str = "realistic") -> str:
    return get()["video"].get(purpose if purpose in VIDEO_PURPOSES else "realistic") or settings.video_engine


def image_engine() -> str:
    return get()["image_engine"] or settings.image_engine


# ---------------- readiness (never triggers a download) ----------------

def video_ready(engine: str) -> bool:
    from cvm.models_registry import is_ready
    from cvm.visuals import sources

    if engine == "ltx":
        return sources.ltx_ready()
    if engine == "wan":
        return is_ready("wan-ti2v-5b") and (settings.models_dir / "MLXGen" / ".venv" / "bin" / "mlxgen").exists()
    return False


def image_ready(engine: str) -> bool:
    from cvm.models_registry import MLX, is_ready

    if engine == "z-image":
        return MLX and is_ready("z-image-turbo")
    if engine == "flux":
        return is_ready("flux-schnell")
    return False


VIDEO_LABELS = {"ltx": "LTX (rápido, mais fiel à imagem)", "wan": "Wan 2.2 (mais lento)"}
IMAGE_LABELS = {"z-image": "Z-Image Turbo (fotográfico)", "flux": "FLUX schnell"}


def options() -> dict:
    return {
        "video": [{"id": e, "label": VIDEO_LABELS[e], "ready": video_ready(e)} for e in VIDEO_ENGINES],
        "image": [{"id": e, "label": IMAGE_LABELS[e], "ready": image_ready(e)} for e in IMAGE_ENGINES],
    }


class NotReady(ValueError):
    """A model that is not fully downloaded on this machine was picked."""


# ---------------- engine per feature on this machine (hardware tiers, cvm/setup/requirements.py) ----------------
# Setting "engines" → "features": {feature: engine} written by the wizard's engine test (the tier that passed) or by
# the person's pick among the tested ones. Without a saved choice the pipeline uses the best tier that fits this
# machine's memory and has not failed its test (Setting "engine_tests").
TIER_FEATURES = ("ai_images", "animation", "avatar", "music", "kids")
TESTS_KEY = "engine_tests"


def feature_choices() -> dict:
    return {k: v for k, v in (_saved().get("features") or {}).items() if k in TIER_FEATURES and v}


def _failed(feature: str) -> set[str]:
    from sqlmodel import Session

    from cvm import db
    from cvm.models import Setting

    try:
        with Session(db.engine) as s:
            row = s.get(Setting, TESTS_KEY)
            res = ((row.value or {}).get(feature) or {}).get("results") or [] if row else []
    except Exception:  # noqa: BLE001
        return set()
    return {r["engine"] for r in res if r.get("ok") is False}


def feature_engine(feature: str) -> str | None:
    """The engine this machine runs a feature with (a tier's engine id, e.g. "flux-nf4", "ltxv-2b", "latentsync"),
    None when no tier can run here."""
    from cvm.setup import requirements as req

    try:
        hw = req.detected()
    except Exception:  # noqa: BLE001
        hw = None
    platform = hw.platform if hw and hw.platform != "other" else "mac" if sys.platform == "darwin" else "windows"
    t = req.pick(feature, platform, hw, feature_choices().get(feature), failed=_failed(feature))
    return t.engine if t else None


def set_feature_engine(feature: str, engine: str | None) -> None:
    """Save (or clear, with None) this machine's engine for a feature."""
    from sqlmodel import Session

    from cvm import db
    from cvm.models import Setting

    if feature not in TIER_FEATURES:
        raise ValueError(f"feature: {feature!r}")
    with Session(db.engine) as s:
        row = s.get(Setting, KEY) or Setting(key=KEY)
        value = dict(row.value or {})
        feats = dict(value.get("features") or {})
        if engine:
            feats[feature] = engine
        else:
            feats.pop(feature, None)
        row.value = {**value, "features": feats}
        s.add(row)
        s.commit()
    invalidate()


def put(data: dict) -> dict:
    """Validate and save the user's choices; only a changed model choice must be ready (a default left untouched
    while its model is missing still falls back as before). Raises ValueError / NotReady."""
    from sqlmodel import Session

    from cvm import db
    from cvm.models import Setting

    current = get()
    new: dict = {}
    if "writer" in data:
        if data["writer"] not in WRITERS:
            raise ValueError(f"writer: {data['writer']!r}")
        new["writer"] = data["writer"]
    if "codex_fallback" in data:
        new["codex_fallback"] = bool(data["codex_fallback"])
    if "video" in data:
        video = {}
        for purpose, engine in (data["video"] or {}).items():
            if purpose not in VIDEO_PURPOSES or engine not in VIDEO_ENGINES:
                raise ValueError(f"video.{purpose}: {engine!r}")
            if engine != current["video"][purpose] and not video_ready(engine):
                raise NotReady(f"{engine} ainda não está baixado nesta máquina")
            video[purpose] = engine
        new["video"] = video
    if "image_engine" in data:
        engine = data["image_engine"]
        if engine not in IMAGE_ENGINES:
            raise ValueError(f"image_engine: {engine!r}")
        if engine != current["image_engine"] and not image_ready(engine):
            raise NotReady(f"{engine} ainda não está baixado nesta máquina")
        new["image_engine"] = engine
    with Session(db.engine) as s:
        row = s.get(Setting, KEY) or Setting(key=KEY)
        saved = dict(row.value or {})
        if "video" in new:
            new["video"] = {**(saved.get("video") or {}), **new["video"]}
        row.value = {**saved, **new}
        s.add(row)
        s.commit()
    invalidate()
    return get()
