"""Script JSON contract shared by the scriptwriter agent, the UI editor and the render stage."""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CameraMove = Literal["push_in", "pull_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
VisualType = Literal["avatar_full", "avatar_pip", "broll", "footage", "headline", "chart", "source_clip", "ai_image", "user_media"]


class ChartItem(BaseModel):
    label: str
    value: float | None = None  # bar/stat: the number
    text: str | None = None  # timeline/stat: what to show instead of a number ("2028")
    highlight: bool = False  # bar: the bar to emphasise


class Chart(BaseModel):
    """Data for `chart` scenes, drawn by cvm/render/chart.py (labels in the script's language)."""

    kind: Literal["bar", "timeline", "stat"] = "bar"
    title: str | None = None
    unit: str = ""  # appended to values ("s", "km/h", "%")
    items: list[ChartItem] = Field(min_length=1)
    source: str | None = None  # outlet shown under the chart


class Visual(BaseModel):
    type: VisualType
    search_terms: list[str] = []  # stock footage / news queries
    image_prompt: str | None = None  # for local FLUX generation
    source_url: str | None = None  # react source clip or article to screenshot
    clip_query: str | None = None  # footage: YouTube search for real video of the subject ("GTA 6 official gameplay trailer")
    official_channels: list[str] = []  # footage: exact channel names that own the subject ("Rockstar Games")
    notes: str | None = None
    motion_prompt: str | None = None  # hero ai_image scenes: the motion to animate (Wan 2.2), e.g. "fog drifts, camera slowly pushes in"
    camera: CameraMove | None = None  # still images: how the virtual camera moves (default: varies by scene)
    chart: Chart | None = None  # chart scenes: the data to draw (without it the scene is a text card)
    media_id: int | None = None  # user_media scenes: id of one of the user's own files (get_project → user_media)
    media_start: float | None = None  # user_media video: second of the clip where this scene starts (see the preview's time labels)
    keep_audio: bool | None = None  # user_media video: play the clip's own sound (the user speaking) instead of narration; None = the file's choice
    flag: str | None = None  # a national flag on screen: ISO-2 code ("AE"); the official flag is used, never an AI drawing
    characters: list[str] = []  # kids series: names of the characters visible in this scene (their reference sheets guide the drawing)


class Scene(BaseModel):
    index: int
    narration: str
    on_screen_text: str | None = None
    visual: Visual
    target_seconds: float | None = None
    speaker: str | None = None  # kids series: the character who says this scene's line (None = narrator)
    delivery: str | None = None  # how this scene is spoken, in English ("slower, almost whispering"); overrides voice_style


THUMB_MAX_WORDS = 3
MAX_SCENE_S = 45  # long videos: a visual change at least every 30–45 s (one scene = one visual)


def _norm_word(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^\w]", "", "".join(c for c in w if not unicodedata.combining(c)))


def _words(text: str) -> list[str]:
    return [w for w in (text or "").split() if _norm_word(w)]


def thumbnail_text_problems(text: str, title: str | None) -> list[str]:
    """Phase 6 rule: at most 3 words, and they ADD to the title instead of repeating it."""
    out = []
    words = _words(text)
    if len(words) > THUMB_MAX_WORDS:
        out.append(f"thumbnail_text tem {len(words)} palavras (máx. {THUMB_MAX_WORDS}): {text!r}")
    if title and words:
        t = {_norm_word(w) for w in _words(title)}
        key = [_norm_word(w) for w in words if len(_norm_word(w)) > 2 or _norm_word(w).isdigit()]
        same = [w for w in key if w in t]
        if key and len(same) * 2 > len(key):
            out.append(f"thumbnail_text repete o título ({' '.join(same)}): escreva palavras que somem ao título")
    return out


def fit_thumbnail_text(text: str, title: str | None = None) -> str:
    """What a thumbnail burns for scripts written before the rule: the words not already in the title (when any are
    left), at most 3."""
    words = _words(text)
    if title:
        t = {_norm_word(w) for w in _words(title)}
        fresh = [w for w in words if _norm_word(w) not in t or not (len(_norm_word(w)) > 2 or _norm_word(w).isdigit())]
        if any(len(_norm_word(w)) > 2 for w in fresh):
            words = fresh
    return " ".join(words[:THUMB_MAX_WORDS])


def _check_thumbnail_text(data):
    """Packaging validation: new packaging must follow the thumbnail text rule (old scripts are marked `_legacy`)."""
    if isinstance(data, dict) and not data.get("_legacy") and isinstance(data.get("thumbnail_text"), str):
        if problems := thumbnail_text_problems(data["thumbnail_text"], data.get("title")):
            raise ValueError("; ".join(problems))
    return data


class PackagingOption(BaseModel):
    title: str
    thumbnail_text: str  # ≤ 3 words burnt on the thumbnail, in the channel's language; adds to the title, never repeats it

    _text_rule = model_validator(mode="before")(classmethod(lambda cls, data: _check_thumbnail_text(data)))


class Packaging(BaseModel):
    """Title + thumbnail decided BEFORE the script: the intro must confirm and extend this promise."""

    title: str
    thumbnail_text: str
    thumbnail_prompt: str | None = None  # FLUX background, English, no text: one bold subject, high contrast; never a flag
    thumbnail_side: Literal["left", "right"] = "right"  # where the presenter stands; the text takes the other side
    thumbnail_scene: int | None = None  # use this scene's own image as the background (kids: the characters) instead of FLUX
    thumbnail_presenter: bool = True  # False: the scene image already shows the presenter (his selfie): no cut-out on top
    alternatives: list[PackagingOption] = []  # up to 2 more for YouTube "Test & Compare"

    _text_rule = model_validator(mode="before")(classmethod(lambda cls, data: _check_thumbnail_text(data)))


HookType = Literal["question", "number", "contrarian", "best_moment"]  # the 4 hook types of the channel plan


class Block(BaseModel):
    """Long videos: the content after the bumper, in blocks of 60–90 s (one chapter each in the description)."""

    title: str  # chapter title in the script's language (≤ 40 chars, no timestamp)
    scenes: list[int] = Field(min_length=1)  # scene indices in this block, in order
    question: str | None = None  # the mini question that opens the block
    reveal: str | None = None  # the answer that closes it
    continuity: str | None = None  # the line that pulls into the next block ("mas isso não é o mais estranho…")


class CtaPoint(BaseModel):
    """A short spoken subscribe ask, tied to the topic: ~40% of the video and the end. Said by the presenter or
    narrator inside that scene's narration (never by the bumper); kids episodes have none."""

    scene: int
    at: Literal["mid", "end"] = "mid"
    text: str | None = None  # the ask as spoken (also in the scene's narration)


class Ending(BaseModel):
    answer: str | None = None  # the hook's promise, answered
    comment_question: str | None = None  # one specific question for the comments
    next_video: str | None = None  # the line sending them to the next video (end screen: "assista este agora")


def _anchor_word(v: str, field: str) -> str:
    from cvm.editorial_text import norm

    if len(v.split()) != 1 or not norm(v):
        raise ValueError(f"{field} precisa ser uma única palavra da narração (recebido: {v!r})")
    return v


class Proof(BaseModel):
    """A proof scene: the page the claim comes from. `lang` is the page's language, set only when the scriptwriter knows it."""
    url: str
    frase: str  # the sentence on the page that carries the claim
    destaque_em: str | None = None
    traducao: str | None = None

    @field_validator("destaque_em")
    @classmethod
    def _one_word(cls, v):
        return _anchor_word(v, "destaque_em") if v is not None else v

    lang: str | None = None


class Search(BaseModel):
    termos: list[str] = Field(min_length=1)
    preferir: Literal["oficial", "noticia", "banco", "meus"] = "banco"
    official_channels: list[str] = []


class EditorialScene(BaseModel):
    """One news-desk scene, anchored to the narration word `em` (it starts when that word is spoken)."""
    em: str
    tipo: Literal["pergunta", "impacto", "numero", "lista", "prova", "video", "foto"]
    secao: tuple[str, str] | None = None
    texto: str | None = None
    sub: str | None = None
    pequeno: str | None = None
    valor: str | None = None
    rotulo: str | None = None
    fonte: str | None = None
    itens: list[tuple[str, str]] = []
    estilo: str | None = None
    prova: Proof | None = None
    busca: Search | None = None
    mov: Literal["in", "out", "punch", "parado", "pan-e", "pan-d"] | None = None
    som: Literal["whoosh", "pop", "impacto", "riser"] | None = None
    varre: bool = False
    flash: bool = False
    impacto_em: str | None = None  # impacto: the word on which the big words slam in (cards.html `impacto_t`)

    @field_validator("em")
    @classmethod
    def _em_one_word(cls, v):
        return _anchor_word(v, "em")

    @field_validator("impacto_em")
    @classmethod
    def _impacto_one_word(cls, v):
        return _anchor_word(v, "impacto_em") if v is not None else v

    @field_validator("itens")
    @classmethod
    def _itens_anchor(cls, v):
        for a, _ in v:
            _anchor_word(a, "âncora do item")
        return v


class Editorial(BaseModel):
    cenas: list[EditorialScene] = Field(min_length=8)


class ScriptContent(BaseModel):
    hook: str  # the first line (0–5 s)
    hook_type: HookType | None = None
    promise: str | None = None  # 5–20 s: what they get by watching to the end ("no final eu mostro…")
    packaging: Packaging | None = None
    intro_end: int | None = None  # index of the scene that ends the promise: the short bumper plays right after it (long videos)
    blocks: list[Block] = []  # long videos: the content in 60–90 s blocks (chapters); empty in old scripts and Shorts
    cta_points: list[CtaPoint] = []  # spoken subscribe asks (~40% and the end)
    ending: Ending | None = None
    scenes: list[Scene] = Field(min_length=1)
    call_to_action: str | None = None
    target_duration_s: int = 60
    sources: list[str] = []  # URLs used for fact checking / credits
    voice_style: str | None = None  # overall delivery in English ("calm, low, suspenseful storyteller")
    hook_variants: list[str] = []  # alternative first lines (different hook patterns) for the human to pick
    cast: dict[str, str] = {}  # kids series: character name → English voice description ("small fox cub girl, high, bright, playful")
    # kids series: character name → English look + art style ("a small orange fox cub with big green eyes…, 3D animated
    # cartoon, soft Pixar-like style"); the first time, the character's reference sheet is drawn from it (cvm/visuals/cast.py)
    cast_looks: dict[str, str] = {}
    editorial: Editorial | None = None  # news-desk format: scenes anchored to narration words
    pronunciation: dict[str, str] = {}  # word as written → how the TTS should read it ("Take-Two": "Teik Tu")

    @model_validator(mode="before")
    @classmethod
    def _legacy_packaging(cls, data):
        """Scripts written before the retention structure (no blocks) keep validating: their thumbnail words are
        fitted to the new rule instead of rejected (the thumbnail burns the fitted text too)."""
        if isinstance(data, dict) and not data.get("blocks") and isinstance(pkg := data.get("packaging"), dict):
            def fit(o: dict) -> dict:
                if not isinstance(o, dict):
                    return o
                return {**o, "_legacy": True, **({"thumbnail_text": fit_thumbnail_text(o["thumbnail_text"], o.get("title"))}
                                                 if isinstance(o.get("thumbnail_text"), str) else {})}

            pkg = fit(pkg)
            if isinstance(pkg.get("alternatives"), list):
                pkg = {**pkg, "alternatives": [fit(a) for a in pkg["alternatives"]]}
            data = {**data, "packaging": pkg}
        return data

    @model_validator(mode="after")
    def _structure(self):
        if not self.blocks:
            return self
        idx = {s.index for s in self.scenes}
        bad = [i for b in self.blocks for i in b.scenes if i not in idx] + [c.scene for c in self.cta_points if c.scene not in idx]
        if self.intro_end is not None and self.intro_end not in idx:
            bad.append(self.intro_end)
        if bad:
            raise ValueError(f"blocks/cta_points/intro_end citam cenas que não existem: {sorted(set(bad))}")
        long = [s.index for s in self.scenes if (s.target_seconds or 0) > MAX_SCENE_S and not s.visual.keep_audio]  # a recorded part plays whole
        if long:
            raise ValueError(f"cenas acima de {MAX_SCENE_S} s (troque o visual a cada 30–45 s): {long}")
        return self


EDITORIAL_TEXT_MAX = 60
EDITORIAL_VALUE_MAX = 12


def editorial_problems(content: dict, video_language: str) -> list[str]:
    """Rule violations of an editorial script (pt-BR messages; empty = fine).

    Anchors: each scene's `em` must appear in the joined narration, in scene order (compared through
    `editorial_text.norm`); its inner anchors (`itens` words, `prova.destaque_em`, `impacto_em`) must be said from
    that word up to the next scene's anchor, as cvm/render/editorial/timeline.py times them. Every `prova.url` must be in `sources`. Proof pages in another language than the video
    need `traducao`, but only when the scene's `prova.lang` is declared and differs from the video language prefix;
    an unknown page language imposes nothing."""
    from cvm.editorial_text import norm

    ed = content.get("editorial")
    if not ed:
        return []
    out: list[str] = []
    words = [norm(w) for sc in content.get("scenes") or [] for w in str(sc.get("narration", "")).split()]
    sources = set(content.get("sources") or [])
    prefix = video_language.split("-")[0].lower()
    cenas = ed.get("cenas") or []
    pos = 0
    at: list[int | None] = []  # index of each scene's anchor word (None: not found)
    for i, c in enumerate(cenas):
        em = c.get("em", "")
        try:
            j = words.index(norm(em), pos)
            at.append(j)
            pos = j + 1
        except ValueError:
            at.append(None)
            out.append(f"cena {i}: a âncora \"{em}\" não aparece na narração depois da âncora anterior (ordem das palavras)")
    for i, c in enumerate(cenas):
        tipo = c.get("tipo")
        if at[i] is not None:  # inner anchors: said from the scene's own word up to the next scene's (timeline.rel)
            end = next((j for j in at[i + 1:] if j is not None), len(words))
            inner = [("item", a) for a, *_ in (c.get("itens") or []) if isinstance(a, str)]
            hl = (c.get("prova") or {}).get("destaque_em")
            inner += [("prova.destaque_em", x) for x in (hl if isinstance(hl, list) else [hl] if hl else [])]
            if c.get("impacto_em"):
                inner.append(("impacto_em", c["impacto_em"]))
            for what, a in inner:
                if norm(a) not in words[at[i]:end]:
                    out.append(f"cena {i}: a âncora de {what} \"{a}\" não é dita entre a âncora da cena "
                               f"(\"{c.get('em', '')}\") e a da cena seguinte")
        if tipo in ("pergunta", "impacto") and len(c.get("texto") or "") > EDITORIAL_TEXT_MAX:
            out.append(f"cena {i}: texto de {tipo} com mais de {EDITORIAL_TEXT_MAX} caracteres")
        if tipo == "numero" and len(c.get("valor") or "") > EDITORIAL_VALUE_MAX:
            out.append(f"cena {i}: valor do número com mais de {EDITORIAL_VALUE_MAX} caracteres")
        if tipo == "lista" and not 1 <= len(c.get("itens") or []) <= 5:
            out.append(f"cena {i}: lista precisa de 1 a 5 itens")
        if tipo == "prova":
            p = c.get("prova") or {}
            if p.get("url") not in sources:
                out.append(f"cena {i}: a URL da prova não está em sources")
            lang = (p.get("lang") or "").split("-")[0].lower()
            if lang and lang != prefix and not p.get("traducao"):
                out.append(f"cena {i}: prova em {lang} num vídeo em {prefix} precisa de traducao")
    return out
