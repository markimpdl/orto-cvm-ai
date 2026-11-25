"""National flags are NEVER drawn by an AI model (a UAE video once showed a Palestine-like red triangle instead of
the UAE flag): a scene or thumbnail about a flag gets the official flag from Wikimedia Commons, laid flat on a
frame, or — when the country is unknown — real stock instead of an AI image.

- `mentions_flag(text)`: the text asks for a flag (en/pt/es words, Arabic علم only next to a country, since
  العلم alone also means "science", and إعلام is "media").
- `country_of(text)`: ISO-2 code of the first country named (names/demonyms in en/pt/es/ar), else None.
- `official_flag(code, out, portrait)`: 1080x1920 / 1920x1080 frame with the official flag, never stretched.
- `scene_flag(scene)`: (is a flag scene, ISO code or None) from visual.flag / image_prompt / search_terms /
  on_screen_text (the narration only names the country when it names exactly one).
"""

import logging
import re
import unicodedata
from pathlib import Path

import httpx

from cvm.config import settings
from cvm.visuals.sources import UA

log = logging.getLogger(__name__)
SOURCE = "Wikimedia Commons (bandeira oficial)"
LICENSE = "public domain"
COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=1600"

# ISO-2 → Commons file name of the official flag
COMMONS_FILES = {
    "AE": "Flag_of_the_United_Arab_Emirates.svg", "SA": "Flag_of_Saudi_Arabia.svg", "QA": "Flag_of_Qatar.svg",
    "KW": "Flag_of_Kuwait.svg", "OM": "Flag_of_Oman.svg", "BH": "Flag_of_Bahrain.svg", "EG": "Flag_of_Egypt.svg",
    "JO": "Flag_of_Jordan.svg", "LB": "Flag_of_Lebanon.svg", "PS": "Flag_of_Palestine.svg", "IQ": "Flag_of_Iraq.svg",
    "SY": "Flag_of_Syria.svg", "YE": "Flag_of_Yemen.svg", "MA": "Flag_of_Morocco.svg", "DZ": "Flag_of_Algeria.svg",
    "TN": "Flag_of_Tunisia.svg", "LY": "Flag_of_Libya.svg", "SD": "Flag_of_Sudan.svg", "TR": "Flag_of_Turkey.svg",
    "IR": "Flag_of_Iran.svg", "IL": "Flag_of_Israel.svg", "BR": "Flag_of_Brazil.svg",
    "US": "Flag_of_the_United_States.svg", "CA": "Flag_of_Canada_(Pantone).svg", "MX": "Flag_of_Mexico.svg",
    "AR": "Flag_of_Argentina.svg", "CL": "Flag_of_Chile.svg", "CO": "Flag_of_Colombia.svg",
    "PT": "Flag_of_Portugal.svg", "ES": "Flag_of_Spain.svg", "FR": "Flag_of_France.svg", "DE": "Flag_of_Germany.svg",
    "IT": "Flag_of_Italy.svg", "GB": "Flag_of_the_United_Kingdom.svg", "IE": "Flag_of_Ireland.svg",
    "NL": "Flag_of_the_Netherlands.svg", "BE": "Flag_of_Belgium.svg", "CH": "Flag_of_Switzerland.svg",
    "RU": "Flag_of_Russia.svg", "UA": "Flag_of_Ukraine.svg", "PL": "Flag_of_Poland.svg", "IN": "Flag_of_India.svg",
    "PK": "Flag_of_Pakistan.svg", "BD": "Flag_of_Bangladesh.svg", "CN": "Flag_of_the_People's_Republic_of_China.svg",
    "JP": "Flag_of_Japan.svg", "KR": "Flag_of_South_Korea.svg", "ID": "Flag_of_Indonesia.svg",
    "PH": "Flag_of_the_Philippines.svg", "AU": "Flag_of_Australia_(converted).svg", "NZ": "Flag_of_New_Zealand.svg",
    "ZA": "Flag_of_South_Africa.svg", "NG": "Flag_of_Nigeria.svg", "KE": "Flag_of_Kenya.svg",
}

# Latin-script names/demonyms (en/pt/es), matched on lowercased, accent-free text with word boundaries
_LATIN = {
    "AE": r"united arab emirates|u\.?a\.?e\.?|emirates|emiratis?|emirados(?: arabes unidos)?|emiratos(?: arabes unidos)?|"
          r"emiradense|dubai|abu dhabi",
    "SA": r"saudi(?: arabia)?|saudis|arabia saudita|sauditas?",
    "QA": r"qatar|qatari|catar|catari|catarense",
    "KW": r"kuwait|kuwaiti|kuwaitiano",
    "OM": r"oman|omani|omanense|sultanato de oma",
    "BH": r"bahrain|bahraini|bahrein|barein|bareinita",
    "EG": r"egypt|egyptian|egito|egipto|egipcios?|egipcias?",
    "JO": r"jordan|jordanian|jordania|jordanianos?",
    "LB": r"lebanon|lebanese|libano|libanes|libanesa",
    "PS": r"palestine|palestinians?|palestina|palestinos?",
    "IQ": r"iraq|iraqis?|iraque|iraquianos?|irak",
    "SY": r"syria|syrians?|siria|sirios?",
    "YE": r"yemen|yemenis?|iemen|iemenitas?",
    "MA": r"morocco|moroccans?|marrocos|marroquinos?|marruecos|marroquies",
    "DZ": r"algeria|algerians?|argelia|argelinos?",
    "TN": r"tunisia|tunisians?|tunisianos?|tunez",
    "LY": r"libya|libyans?|libia|libios?",
    "SD": r"(?<!south )(?<!sul do )(?<!del sur )sudan|(?<!sul-)sudanese|sudao|sudaneses?",
    "TR": r"turkey|turkish|turkiye|turquia|turcos?|turcas?",
    "IR": r"iran|iranians?|iranianos?|irani",
    "IL": r"israel|israelis?|israelenses?",
    "BR": r"brazil|brazilians?|brasil|brasileiros?|brasileiras?|brasilenos?",
    "US": r"united states(?: of america)?|(?<!latin )(?<!south )(?<!central )(?<!north )america(?! latina| do sul| central| del sur)|"
          r"americans?|estados unidos|eua|norte-americanos?|estadounidenses?",
    "CA": r"canada|canadians?|canadenses?|canadienses?",
    "MX": r"mexico|mexicans?|mexicanos?",
    "AR": r"argentina|argentine|argentinians?|argentinos?",
    "CL": r"chile|chileans?|chilenos?",
    "CO": r"colombia|colombians?|colombianos?",
    "PT": r"portugal|portuguese|portugueses?|portuguesas?",
    "ES": r"spain|spaniards?|espanha|espana|espanhois",
    "FR": r"france|french|franca|francia|franceses?|francesas?",
    "DE": r"germany|germans?|alemanha|alemania|alemaes|alemanes",
    "IT": r"italy|italians?|italia|italianos?|italianas?",
    "GB": r"united kingdom|great britain|britain|british|reino unido|britanicos?|britanicas?",
    "IE": r"ireland|irish|irlanda|irlandeses?",
    "NL": r"netherlands|dutch|holland|holanda|paises baixos|paises bajos|holandeses?",
    "BE": r"belgium|belgians?|belgica|belgas?",
    "CH": r"switzerland|swiss|suica|suiza|suicos?|suizos?",
    "RU": r"russia|russians?|russos?|russas?|rusia|rusos?",
    "UA": r"ukraine|ukrainians?|ucrania|ucranianos?",
    "PL": r"poland|polonia|poloneses?|polacos?",
    "IN": r"india|indiano|indianos",
    "PK": r"pakistan|pakistanis?|paquistao|paquistaneses?|paquistan",
    "BD": r"bangladesh|bangladeshis?|bangladeche",
    "CN": r"china|chinese|chineses?|chinesas?|chinos?",
    "JP": r"japan|japanese|japao|japon|japoneses?|japonesas?",
    "KR": r"(?<!north )korea|(?<!north )koreans?|coreia(?! do norte)|corea(?! del norte)|(?<!norte-)coreanos?",
    "ID": r"indonesia|indonesians?|indonesios?",
    "PH": r"philippines|filipinos?|filipinas",
    "AU": r"australia|australians?|australianos?",
    "NZ": r"new zealand|nova zelandia|nueva zelanda|neozelandeses?",
    "ZA": r"south africa|south africans?|africa do sul|sul-africanos?|sudafrica",
    "NG": r"nigeria|nigerians?|nigerianos?",
    "KE": r"kenya|kenyans?|quenia|quenianos?",
}
# written with diacritics that matter (Irã, Omã: "ira"/"oma" alone are other words) or case (US, UK)
_CASED = {"IR": r"\birã\b", "OM": r"\bomã\b", "US": r"(?<![A-Za-z])(?:U\.?S\.?A?\.?)(?![A-Za-z])", "GB": r"\bUK\b"}

# Arabic stems (alef forms normalised, without ال), allowing و/ف/ب/ك/ل + ال before and ي/ية/يون/يين after
_ARABIC = {
    "AE": "امارات|دولة الامارات|دبي|ابوظبي|ابو ظبي", "SA": "سعودية|سعودي|مملكة العربية السعودية", "QA": "قطر",
    "KW": "كويت", "OM": "عمان", "BH": "بحرين", "EG": "مصر", "JO": "اردن", "LB": "لبنان", "PS": "فلسطين",
    "IQ": "عراق", "SY": "سوريا|سورية", "YE": "يمن", "MA": "مغرب", "DZ": "جزائر", "TN": "تونس", "LY": "ليبيا",
    "SD": "سودان", "TR": "تركيا", "IR": "ايران", "IL": "اسرائيل", "BR": "برازيل",
    "US": "ولايات المتحدة|امريكا|اميركا", "CA": "كندا", "MX": "مكسيك", "AR": "ارجنتين", "CL": "تشيلي",
    "CO": "كولومبيا", "PT": "برتغال", "ES": "اسبانيا", "FR": "فرنسا", "DE": "المانيا", "IT": "ايطاليا",
    "GB": "مملكة المتحدة|بريطانيا", "IE": "ايرلندا", "NL": "هولندا", "BE": "بلجيكا", "CH": "سويسرا", "RU": "روسيا",
    "UA": "اوكرانيا", "PL": "بولندا", "IN": "(?<=ال)هند", "PK": "باكستان", "BD": "بنغلاديش", "CN": "صين", "JP": "يابان",
    "KR": "كوريا الجنوبية|كوريا(?! الشمالية)", "ID": "اندونيسيا", "PH": "فلبين", "AU": "استراليا",
    "NZ": "نيوزيلندا", "ZA": "جنوب افريقيا", "NG": "نيجيريا", "KE": "كينيا",
}
_AR = "؀-ۿ"
_AR_PRE = r"(?:[وفبكل]?ال|لل|[وفبكل])?"
_AR_SUF = r"(?:ية|يه|يون|يين|ي)?"


def _ascii(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _arabic(text: str) -> str:
    text = re.sub("[ً-ْـ]", "", text)  # tashkeel, tatweel
    return re.sub("[أإآ]", "ا", text)


_LATIN_RE = {code: re.compile(rf"(?<![a-z])(?:{p})(?![a-z])") for code, p in _LATIN.items()}
_CASED_RE = {code: re.compile(p) for code, p in _CASED.items()}
_ARABIC_RE = {code: re.compile(rf"(?<![{_AR}]){_AR_PRE}(?:{p}){_AR_SUF}(?![{_AR}])") for code, p in _ARABIC.items()}


def countries_in(text: str | None) -> list[str]:
    """Every country named in the text, in order of first appearance."""
    if not text:
        return []
    low, ar = _ascii(text), _arabic(text)
    hits: dict[str, int] = {}
    for table, src in ((_LATIN_RE, low), (_CASED_RE, text), (_ARABIC_RE, ar)):
        for code, rx in table.items():
            if (m := rx.search(src if table is not _CASED_RE else (src.lower() if code in ("IR", "OM") else src))):
                hits[code] = min(hits.get(code, m.start()), m.start())
    return sorted(hits, key=hits.get)


def country_of(text: str | None) -> str | None:
    found = countries_in(text)
    return found[0] if found else None


_FLAG_WORDS = re.compile(
    r"(?<![a-z])(?:flags?|flagpoles?|flag-waving|bandeiras?|bandeirinhas?|banderas?|banderines?|"
    r"national colou?rs|cores nacionais|colores nacionales|national ensign|national banner)(?![a-z])")
_IDIOMS = re.compile(r"(?<![a-z])(?:red|green|yellow|white|checkered|chequered) flags?(?![a-z])|"
                     r"bandeiras? (?:vermelhas?|verdes?)(?![a-z])|flag (?:it|this|that|them)(?![a-z])")
_BANNER = re.compile(r"(?<![a-z])(?:banners?|estandartes?|pavilhao|pendoes?)(?![a-z])")


def _arabic_flag(text: str) -> bool:
    if re.search(r"(?<![؀-ۿ])(?:أعلام|راية|رايات|الراية|الرايات|برايات|براية|والراية|بيرق|البيرق)(?![؀-ۿ])",
                 text):  # أعلام only with its hamza: اعلام/إعلام is "media"
        return True
    ar = _arabic(text)
    if re.search(rf"(?<![{_AR}]){_AR_PRE}علم(?:ها|ه)?\s+(?:ال)?دول", ar):  # علم الدولة
        return True
    names = "|".join(_ARABIC.values())
    # علم / أعلام next to a country ("علم الإمارات", "العلم الإماراتي"); العلم alone can mean "science"
    return bool(re.search(rf"(?<![{_AR}]){_AR_PRE}(?:علم|اعلام)\s+{_AR_PRE}(?:{names}){_AR_SUF}(?![{_AR}])", ar))


def mentions_flag(text: str | None) -> bool:
    """True when the text asks to show a (national) flag. Idioms ("red flags", "bandeira vermelha") don't count."""
    if not text:
        return False
    low = _ascii(text)
    if _FLAG_WORDS.search(_IDIOMS.sub(" ", low)):
        return True
    if _BANNER.search(low) and country_of(text):  # "banner of Brazil"
        return True
    return _arabic_flag(text)


def _scene_texts(scene: dict) -> str:
    v = scene.get("visual") or {}
    return " | ".join(str(x) for x in [v.get("image_prompt"), *(v.get("search_terms") or []), scene.get("on_screen_text")] if x)


def scene_flag(scene: dict) -> tuple[bool, str | None]:
    """(the scene is about a flag, ISO-2 code or None). visual.flag = "AE" is the writer's explicit request."""
    v = scene.get("visual") or {}
    code = str(v.get("flag") or "").strip().upper() or None
    if code == "UK":
        code = "GB"
    texts = _scene_texts(scene)
    if not code and not mentions_flag(texts):
        return False, None
    if code not in COMMONS_FILES:
        code = country_of(texts)
        if not code:  # the narration may say whose flag it is — only when it names exactly one country
            named = countries_in(scene.get("narration"))
            code = named[0] if len(named) == 1 else None
    return True, code


def _download(code: str) -> Path | None:
    """The flag as a PNG (~1600 px wide), cached in data/flags/<code>.png. None on any network/format failure."""
    from PIL import Image

    cache = settings.data_dir / "flags" / f"{code}.png"
    if cache.exists():
        return cache
    name = COMMONS_FILES.get(code)
    if not name:
        return None
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".part")
    try:
        r = httpx.get(COMMONS.format(name=name), headers=UA, timeout=60, follow_redirects=True)
        r.raise_for_status()
        tmp.write_bytes(r.content)
        with Image.open(tmp) as im:
            im.convert("RGBA").save(cache)
        return cache
    except Exception as e:  # noqa: BLE001
        log.warning("official flag %s not downloaded: %s", code, e)
        return None
    finally:
        tmp.unlink(missing_ok=True)


def compose(flag_png: Path, out: Path, portrait: bool) -> Path:
    """The flag flat and centred at its own aspect ratio over a dark, blurred wash of its colours."""
    from PIL import Image, ImageEnhance, ImageFilter

    W, H = (1080, 1920) if portrait else (1920, 1080)
    flag = Image.open(flag_png).convert("RGBA")
    flat = Image.new("RGB", flag.size, (20, 20, 24))
    flat.paste(flag, mask=flag.split()[3])
    # background: the flag scaled to cover, heavily blurred and darkened
    s = max(W / flat.width, H / flat.height)
    bg = flat.resize((max(W, round(flat.width * s)), max(H, round(flat.height * s))), Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(60))
    bg = ImageEnhance.Brightness(bg).enhance(0.28)
    # foreground: uniform scale only (never stretched)
    k = min(W * (0.86 if portrait else 0.7) / flat.width, H * 0.7 / flat.height)
    fw, fh = max(1, round(flat.width * k)), max(1, round(flat.height * k))
    fg = flat.resize((fw, fh), Image.LANCZOS)
    x, y = (W - fw) // 2, (H - fh) // 2
    shadow = Image.new("L", (W, H), 0)
    shadow.paste(150, (x + 12, y + 18, x + fw + 12, y + fh + 18))
    bg.paste((0, 0, 0), mask=shadow.filter(ImageFilter.GaussianBlur(24)))
    bg.paste(fg, (x, y))
    out.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out)
    return out


def official_flag(code: str | None, out: Path, portrait: bool) -> Path | None:
    """Frame (1080x1920 portrait / 1920x1080 landscape) with the official flag of `code`; None when unknown or
    the download fails — callers then use real stock or a video frame, never an AI drawing."""
    code = (code or "").upper()
    code = "GB" if code == "UK" else code
    if code not in COMMONS_FILES:
        return None
    if out.exists():
        return out
    src = _download(code)
    if not src:
        return None
    try:
        return compose(src, out, portrait)
    except Exception as e:  # noqa: BLE001
        log.warning("official flag %s not composed: %s", code, e)
        return None
