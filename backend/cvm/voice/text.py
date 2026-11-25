"""Text preparation for TTS: numbers spelled out in the target language, and sentence chunks.

TTS models read digits unreliably (especially Arabic and pt-BR years, prices and percentages), so we spell
them out first. Narration is then synthesised one sentence at a time: shorter inputs keep the cloned voice
stable, and a bad sentence can be regenerated alone.
"""

import re

from num2words import num2words

N2W_LANG = {"pt-BR": "pt_BR", "ar-AE": "ar", "en-US": "en"}
PERCENT = {"pt-BR": "por cento", "ar-AE": "بالمئة", "en-US": "percent"}
CURRENCY = {  # symbol → (pt-BR, ar-AE, en-US) word, said after the number
    "R$": ("reais", "ريال برازيلي", "reais"),
    "US$": ("dólares", "دولار", "dollars"),
    "$": ("dólares", "دولار", "dollars"),
    "€": ("euros", "يورو", "euros"),
    "AED": ("dirhams", "درهم", "dirhams"),
}
_LANGS = ("pt-BR", "ar-AE", "en-US")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_ABBREV = {
    "pt-BR": ["Ep", "Sr", "Sra", "Srta", "Dr", "Dra", "Prof", "Profa", "etc", "ex", "nº", "aprox", "vs"],
    "en-US": ["Ep", "Mr", "Mrs", "Ms", "Dr", "Prof", "St", "etc", "vs", "approx", "No"],
    "ar-AE": [],
}
SAY_ABBREV = {  # read aloud in full; the TTS otherwise spells or skips them
    "pt-BR": {"Dr": "Doutor", "Dra": "Doutora", "Sr": "Senhor", "Sra": "Senhora", "Srta": "Senhorita",
              "Prof": "Professor", "Profa": "Professora", "etc": "etcétera", "vs": "versus", "nº": "número"},
    "en-US": {"Mr": "Mister", "Mrs": "Missus", "Dr": "Doctor", "Prof": "Professor", "etc": "et cetera",
              "vs": "versus", "approx": "approximately"},
    "ar-AE": {},
}
MAX_CHARS = 220  # longer sentences are split at commas; Chatterbox drifts on long inputs
MIN_WORDS = 3  # tiny fragments ("Sim.") are glued to a neighbour so prosody stays natural


def _parse_number(raw: str, language: str) -> float | int:
    """'1.234,5' (pt) / '1,234.5' (en) / '1234' → number."""
    if language == "pt-BR":
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    return float(raw) if "." in raw else int(raw)


def _words(n: float | int, language: str, to: str = "cardinal", raw: str | None = None) -> str:
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if language == "ar-AE":
        return _arabic(n, to, raw)
    text = num2words(n, lang=N2W_LANG[language], to=to)
    if to != "year":  # "mil, novecentos" / "one thousand, two hundred and fifty" → read without the pause
        text = text.replace(",", "")
        if language == "en-US":
            text = re.sub(r"\s+and\s+", " ", text)
    return text


# Spoken Gulf/Emirati forms: num2words writes literary nominative Arabic ("تسعة و تسعون", "ألفان", "أربعمائة");
# people in the UAE say "تسعة وتسعين", "ألفين", "أربعمية", with "و" joined to the next word.
_AR_SPOKEN = [
    (r"(عشر|ثلاث|أربع|خمس|ست|سبع|ثمان|تسع)ون\b", r"\1ين"),
    (r"\bاثنان\b", "اثنين"), (r"\b(ألفان|ألفا)\b", "ألفين"), (r"\b(مائتان|مائتا|مئتان|مئتا)\b", "ميتين"),
    (r"\b(مليونان|مليونا)\b", "مليونين"),
    (r"مائة\b", "مية"), (r"\bألفاً\b", "ألف"), (r"\bمليوناً\b", "مليون"),
]


def _arabic(n: float | int, to: str, raw: str | None) -> str:
    """Arabic number words. num2words garbles decimals (7.6 → "سبعة , ستون"): the integer part and the digits
    after the point are read separately, joined by "فاصلة", as people say them."""
    if isinstance(n, float):
        whole, frac = (raw or repr(n)).replace(",", "").split(".", 1)
        zeros = len(frac) - len(frac.lstrip("0"))
        tail = " ".join(["صفر"] * zeros + ([num2words(int(frac), lang="ar")] if frac.strip("0") else []))
        text = f"{num2words(int(whole or 0), lang='ar')} فاصلة {tail}"
    else:
        text = num2words(n, lang="ar", to="ordinal" if to == "ordinal" else "cardinal")
    text = re.sub(r"\s*,\s*", " ", text)
    for pat, rep in _AR_SPOKEN:
        text = re.sub(pat, rep, text)
    return re.sub(r"\bو\s+", "و", re.sub(r"\s+", " ", text)).strip()


_NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
MONTHS = {
    "pt-BR": "janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro dezembro".split(),
    "en-US": "January February March April May June July August September October November December".split(),
}
SINGULAR = {"reais": "real", "dólares": "dólar", "dollars": "dollar", "euros": "euro", "dirhams": "dirham"}
CENTS = {"pt-BR": ("centavo", "centavos"), "en-US": ("cent", "cents")}
_SCALE = {"pt-BR": r"mil|milhão|milhões|bilhão|bilhões|trilhão|trilhões", "en-US": r"thousand|million|billion|trillion"}


def _year(y: str, language: str) -> str:
    n = int(y)
    if len(y) == 2:
        n += 2000 if n < 70 else 1900
    return _words(n, language, "year" if language == "en-US" else "cardinal")


def _dates(text: str, language: str) -> str:
    """12/11/2026 → "doze de novembro de dois mil e vinte e seis" (pt: d/m/y); "November twelfth, twenty
    twenty-six" (en: m/d/y). Only valid day/month pairs are read as dates."""
    def date(m: re.Match) -> str:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        day, month = (a, b) if language == "pt-BR" else (b, a)
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group(0)
        name = MONTHS[language][month - 1]
        if language == "pt-BR":
            out = f"{'primeiro' if day == 1 else _words(day, language)} de {name}"
            return f"{out} de {_year(y, language)}" if y else out
        out = f"{name} {_words(day, language, 'ordinal')}"
        return f"{out}, {_year(y, language)}" if y else out

    return re.sub(r"(?<![\w/.,])(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?(?![\w/])", date, text)


def _hour_pt(h: int) -> str:
    """Hours are feminine in Portuguese: "uma", "duas", "vinte e uma"."""
    return re.sub(r"\bdois$", "duas", re.sub(r"\bum$", "uma", _words(h, "pt-BR")))


def _times(text: str, language: str) -> str:
    if language == "pt-BR":  # 18:30 / 18h30 → "dezoito e trinta"; 18:00 / 18h → "dezoito horas"
        def pt(m: re.Match) -> str:
            h, mm = int(m.group(1)), m.group(2) or m.group(3)
            if m.group(2) and h > 23:
                return m.group(0)
            if mm and mm != "00":
                return f"{_hour_pt(h)} e {_words(int(mm), language)}"
            return f"{_hour_pt(h)} {'hora' if h <= 1 else 'horas'}"

        return re.sub(r"(?<![\w.,:/])(\d{1,2})(?::([0-5]\d)(?:h|min)?|h(?:([0-5]\d)(?:min)?)?)(?![\w:])", pt, text)

    def en(m: re.Match) -> str:  # 9:30 am → "nine thirty a m"; 9:00 → "nine o'clock"; 9:05 → "nine oh five"
        h, mm, ap = int(m.group(1)), m.group(2), m.group(3)
        if h > 23:
            return m.group(0)
        out = _words(h, language)
        if mm and mm != "00":
            out += f" oh {_words(int(mm), language)}" if mm[0] == "0" else f" {_words(int(mm), language)}"
        elif not ap:
            out += " o'clock"
        if ap:
            out += f" {ap.lower()} m"
            rest = text[m.end():]
            if m.group(4) and (not rest.strip() or re.match(r"\s+[A-Z]", rest)):
                out += "."  # the period after "a.m." also ended the sentence
        return out

    ampm = r"\s?([aApP])\.?[mM](\.)?(?!\w)"
    text = re.sub(rf"(?<![\w.,:/])(\d{{1,2}}):([0-5]\d)(?:{ampm})?(?![\w:])", en, text)
    return re.sub(rf"(?<![\w.,:/])(\d{{1,2}})(){ampm}", en, text)


def _money(m: re.Match, language: str) -> str:
    """R$ 349,90 → "trezentos e quarenta e nove reais e noventa centavos"; $449.99 → "four hundred forty-nine
    dollars and ninety-nine cents"; R$ 2 mil → "dois mil reais"."""
    sym, raw, scale = m.group(1), m.group(2), (m.group(3) or "").strip()
    cur = CURRENCY[sym][_LANGS.index(language)]
    n = _parse_number(raw, language)
    if scale:
        num = "mil" if (language == "pt-BR" and scale == "mil" and n == 1) else _words(n, language, raw=raw)
        return f"{num} {scale} {'de ' if language == 'pt-BR' and scale != 'mil' else ''}{cur}"
    sep = "," if language == "pt-BR" else "."
    frac = raw.rsplit(sep, 1)[1] if sep in raw else ""
    if len(frac) > 2:
        return f"{_words(n, language, raw=raw)} {cur}"
    whole, cents = int(n), int((frac + "0")[:2]) if frac else 0
    one = SINGULAR.get(cur, cur) if whole == 1 else cur
    if language == "pt-BR" and re.search(r"(milhão|milhões|bilhão|bilhões|trilhão|trilhões)$", _words(whole, language)):
        one = f"de {cur}"
    parts = [f"{_words(whole, language)} {one}"] if whole or not cents else []
    if cents:
        unit = CENTS[language][0] if cents == 1 else CENTS[language][1]
        if sym == "AED":
            unit = "fils"
        parts.append(f"{_words(cents, language)} {unit}")
    return f" {'e' if language == 'pt-BR' else 'and'} ".join(parts)


def _versions(text: str, language: str) -> str:
    """iOS 18.2 → "dezoito ponto dois" (pt: a dot before exactly 3 digits is a thousands separator: 1.250);
    2.1.3 → "two point one point three"."""
    point = "ponto" if language == "pt-BR" else "point"

    def group(g: str) -> str:
        if len(g) > 1 and g[0] == "0":
            return " ".join(_words(int(c), language) for c in g)
        return _words(int(g), language)

    def version(m: re.Match) -> str:
        v = m.group(1)
        if language == "pt-BR" and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", v):
            return v
        return f" {point} ".join(group(g) for g in v.split("."))

    dots = r"(?:\.\d+)+" if language == "pt-BR" else r"(?:\.\d+){2,}"
    return re.sub(rf"(?<![\w.,])(\d+{dots})(?![\w]|[.,]\d)", version, text)


def spell_numbers(text: str, language: str) -> str:
    text = text.translate(_ARABIC_DIGITS)
    num = _NUM
    li = _LANGS.index(language)

    if language == "ar-AE":
        def money(m: re.Match) -> str:
            return f"{_words(_parse_number(m.group(2), language), language, raw=m.group(2))} {CURRENCY[m.group(1)][li]}"

        text = re.sub(rf"(R\$|US\$|AED|\$|€)\s?({num})", money, text)
    else:
        text = _times(_dates(text, language), language)
        text = re.sub(rf"(R\$|US\$|AED|\$|€)\s?({num})(\s(?:{_SCALE[language]})\b)?", lambda m: _money(m, language), text)
    text = re.sub(rf"({num})\s?%", lambda m: f"{_words(_parse_number(m.group(1), language), language, raw=m.group(1))} {PERCENT[language]}", text)
    if language != "ar-AE":
        text = _versions(text, language)
    if language == "pt-BR":  # 1º / 2ª → primeiro / segunda
        text = re.sub(r"(\d+)\s?º", lambda m: _words(int(m.group(1)), language, "ordinal"), text)
        text = re.sub(r"(\d+)\s?ª", lambda m: _words(int(m.group(1)), language, "ordinal")[:-1] + "a", text)
    elif language == "en-US":
        text = re.sub(r"(\d+)(?:st|nd|rd|th)\b", lambda m: _words(int(m.group(1)), language, "ordinal"), text)
        text = re.sub(r"\b(1[1-9]\d\d|20\d\d)\b(?![.,]\d)", lambda m: _words(int(m.group(1)), language, "year"), text)
    return re.sub(rf"(?<![\w.,])({num})(?![\w])", lambda m: _words(_parse_number(m.group(1), language), language, raw=m.group(1)), text)


# Tech terms said the way people say them, before numbers and acronyms are spelled ("32 GB" must not become
# "trinta e dois gê bê"). English words inside Portuguese ("deploy", "ray tracing") are left as written.
UNITS = {  # unit → (pt singular, pt plural, en singular, en plural)
    "GB": ("giga", "gigas", "gigabyte", "gigabytes"), "TB": ("tera", "teras", "terabyte", "terabytes"),
    "MB": ("mega", "megas", "megabyte", "megabytes"), "GHz": ("gigahertz",) * 4, "MHz": ("megahertz",) * 4,
    "kHz": ("quilohertz", "quilohertz", "kilohertz", "kilohertz"), "Hz": ("hertz",) * 4,
}
EPISODE = {"pt-BR": "episódio", "en-US": "episode"}


def _model_number(d: str, language: str) -> str:
    """GPU model numbers are read in pairs: 5090 → "cinquenta noventa", 7900 → "seventy-nine hundred",
    1080 → "dez oitenta"."""
    a, b = int(d[:-2]), d[-2:]
    if b == "00":
        return f"{_words(a, language)} {'cem' if language == 'pt-BR' else 'hundred'}"
    if b[0] == "0":
        return f"{_words(a, language)} {'zero' if language == 'pt-BR' else 'oh'} {_words(int(b), language)}"
    return f"{_words(a, language)} {_words(int(b), language)}"


def spell_tech(text: str, language: str) -> str:
    if language not in EPISODE:
        return text
    li = 0 if language == "pt-BR" else 2
    text = re.sub(r"\bEp\.?\s?(?=\d)", EPISODE[language] + " ", text)
    text = re.sub(r"\bPS([1-5])\b", r"PlayStation \1", text)
    text = re.sub(r"\b(RTX|GTX|RX)\s?(\d{3,4})\b", lambda m: f"{m.group(1)} {_model_number(m.group(2), language)}", text)

    def unit(m: re.Match) -> str:
        forms = UNITS[m.group(2)]
        return f"{m.group(1)} {forms[li] if m.group(1) == '1' else forms[li + 1]}"

    text = re.sub(rf"(?<![\w.,])({_NUM})\s?(GB|TB|MB|GHz|MHz|kHz|Hz)\b", unit, text)
    text = re.sub(r"(?<![\w.,])([2-8])K\b", r"\1 K", text)  # 4K → "quatro K"
    return re.sub(r"(\d)\s?fps\b", r"\1 FPS", text, flags=re.IGNORECASE)  # spelled as letters later


# Acronyms are spelled with the letter names of the narration's language ("IA" → "i a", "UAE" → "you ay ee"), unless
# they are said as a word. The script's `pronunciation` map is applied first and always wins.
LETTERS = {
    "en-US": dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ay bee see dee ee eff jee aitch eye jay kay el em en oh pee cue ar ess tee you vee double-you ex why zee".split())),
    "pt-BR": dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "á bê cê dê é éfe gê agá i jota cá éle ême ene ó pê quê érre ésse tê u vê dáblio xis ípsilon zê".split())),
    "ar-AE": dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "إيه بي سي دي إي إف جي إتش آي جيه كيه إل إم إن أو بي كيو آر إس تي يو في دبليو إكس واي زد".split())),
}
SAID_AS_WORD = {"NASA", "NATO", "UNESCO", "UNICEF", "FIFA", "OPEC", "COVID", "LASER", "RADAR", "SCUBA", "IKEA", "LEGO",
                "OK", "AIDS", "ASAP", "GIF", "JPEG", "PIN", "SIM", "WIFI", "CAPTCHA", "YOLO", "ONU", "OTAN", "SUS", "ENEM",
                "INSS", "IPVA", "IPTU", "PIX", "DETRAN", "PROCON", "ANATEL", "ANVISA", "PETROBRAS", "EMBRAPA", "SEBRAE"}
# in Arabic narration a word-acronym left in Latin letters is mangled: say it the way Arabic speakers do
AR_WORDS = {"NASA": "ناسا", "NATO": "الناتو", "UNESCO": "اليونسكو", "UNICEF": "اليونيسف", "FIFA": "فيفا", "OPEC": "أوبك",
            "COVID": "كوفيد", "OK": "أوكي", "IKEA": "إيكيا", "LEGO": "ليغو", "WIFI": "واي فاي", "GIF": "جيف", "PIN": "بن",
            "SIM": "سيم", "LASER": "ليزر", "RADAR": "رادار", "AIDS": "الإيدز"}
_ROMAN = re.compile(r"^[IVXLC]+$")


def spell_acronyms(text: str, language: str, bare_letters: bool = False) -> str:
    """Acronyms → the language's letter names ("GTA" → "jee tee ay"), or with `bare_letters` just spaced capitals
    ("G T A": the en-US trained voice says those best, user by ear 2026-11-14)."""
    names = LETTERS.get(language)
    if not names:
        return text

    def say(m: re.Match) -> str:
        word = m.group(0)
        if language == "ar-AE" and word in AR_WORDS:
            return AR_WORDS[word]
        if word in SAID_AS_WORD or _ROMAN.match(word):
            return word
        return " ".join(word) if bare_letters else " ".join(names[c] for c in word)

    return re.sub(r"(?<![\w-])[A-Z]{2,5}(?![\w-])", say, text)


# Mixed voice (user, 2026-11-23: "para os trechos que citar siglas como GTA 6, palavras em ingles no meio da frase,
# tenta usar elevenlabs... vamos tentar mesclar"): in pt-BR, a sentence with an acronym or an English word is read by
# ElevenLabs, the rest by the trained voice. Checked on the script's own text (before numbers are spelled out).
_ACRONYM = re.compile(r"(?<![\w-])(?:[A-Z]{2,}\d*|[A-Z]+\d+[A-Z]*)(?![\w-])")  # GTA, PC, PS5, RDNA2, IUCN
_CAMEL = re.compile(r"(?<![\w-])[A-Z][a-z]+[A-Z]\w*")  # GitHub, PlayStation, YouTube
_ENGLISH = re.compile(r"(?<![\wÀ-ÿ])(?=[a-zA-Z]*(?:w|k|y|th|sh|ee|oo|ing\b))[a-zA-Z]{3,}(?![\wÀ-ÿ])")
_NOT_ENGLISH = {"km", "kg", "kids", "show", "shows", "ok", "okay", "wifi", "yas", "kiwi", "tokyo", "york"}
# common English game/tech words without the letters the pattern above looks for
_ENGLISH_WORDS = {"launcher", "launchers", "build", "builds", "port", "ports", "beta", "commit", "commits", "hype",
                  "boot", "download", "downloads", "upload", "gameplay", "game", "games", "trailer", "trailers",
                  "update", "updates", "patch", "mod", "mods", "online", "live", "lives", "streamer", "streamers",
                  "react", "reel", "reels", "post", "posts", "feed", "spoiler", "spoilers", "release", "remaster",
                  "remake", "server", "servers", "cloud", "pixel", "pixels", "frame", "frames", "story", "mode"}


def has_foreign(text: str, language: str) -> bool:
    """A pt-BR sentence the trained voice tends to stumble on: an acronym (GTA 6, PS5), a CamelCase brand (GitHub)
    or an English-looking word (Xbox, Series, shaders, launcher). en-US (the Portuguese-trained voice reading English,
    user 2026-11-24): only acronyms and CamelCase brands (GTA, PS5, GitHub) — every other word is English anyway."""
    if language not in ("pt-BR", "en-US"):
        return False
    if _ACRONYM.search(text) or _CAMEL.search(text):
        return True
    if language == "en-US":
        return False
    if any(w.lower() in _ENGLISH_WORDS for w in re.findall(r"[A-Za-z]+", text)):
        return True
    return any(m.group(0).lower() not in _NOT_ENGLISH for m in _ENGLISH.finditer(text))


def apply_pronunciation(text: str, lexicon: dict[str, str] | None) -> str:
    """Whole-word respellings from the script's `pronunciation` map; longest terms first."""
    for term in sorted(lexicon or {}, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", lexicon[term], text)
    return text


# English words Brazilians say the English way even in Portuguese; the script's own map wins (applied first)
LOANWORDS = {"pt-BR": {"bug": "bâgui", "bugs": "bâguis", "bugado": "bâgado", "bugada": "bâgada"}}


def apply_loanwords(text: str, language: str) -> str:
    for term, said in LOANWORDS.get(language, {}).items():
        text = re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", said, text, flags=re.IGNORECASE)
    return text


# Spoken English contracts (user, 2026-11-14: "you are" → "you're" sounds real). Pronoun + verb only when another word
# follows ("here you are." keeps the full form); negatives always.
_EN_BEFORE_WORD = {"I am": "I'm", "you are": "you're", "we are": "we're", "they are": "they're", "it is": "it's",
                   "that is": "that's", "there is": "there's", "here is": "here's", "what is": "what's",
                   "he is": "he's", "she is": "she's", "I will": "I'll", "you will": "you'll", "we will": "we'll",
                   "they will": "they'll", "it will": "it'll", "I would": "I'd", "you would": "you'd"}
_EN_ALWAYS = {"do not": "don't", "does not": "doesn't", "did not": "didn't", "is not": "isn't", "are not": "aren't",
              "was not": "wasn't", "were not": "weren't", "cannot": "can't", "can not": "can't", "will not": "won't",
              "would not": "wouldn't", "could not": "couldn't", "should not": "shouldn't", "have not": "haven't",
              "has not": "hasn't"}


def contract_english(text: str) -> str:
    def sub(full: str, short: str, after: str, t: str) -> str:
        def keep_case(m: re.Match) -> str:
            return short[0].upper() + short[1:] if m.group(0)[0].isupper() else short
        words = r"\s+".join(map(re.escape, full.split()))
        return re.sub(rf"(?<![\w']){words}(?![\w']){after}", keep_case, t,
                      flags=re.IGNORECASE)

    for full, short in _EN_ALWAYS.items():
        text = sub(full, short, "", text)
    for full, short in _EN_BEFORE_WORD.items():
        text = sub(full, short, r"(?=\s+[A-Za-z])", text)
    return text


# The trained voice (fine-tuned on the user's own reading) says these English names right AS WRITTEN and stumbles on
# the Brazilian respellings the script's `pronunciation` map gives them (by ear, 2026-11-14) — those map entries are
# skipped for it; TRAINED_SAY = the respelling that did sound best with it. Names picked by ear for one person's voice
# go in private/voice/trained_lexicon.json ({"as_written": {lang: [names]}, "say": {lang: {name: respelling}}}),
# added to these.
TRAINED_AS_WRITTEN = {"pt-BR": {"facebook", "google", "instagram", "youtube", "whatsapp", "bug", "bugs"},
                      "en-US": set()}
# en-US: syllable respellings ("Loov-ruh", "Sah-dee-yaht") all sounded wrong with the trained voice
_SYLLABLES = re.compile(r"[A-Za-z]+-[A-Za-z]+")


def _private_lexicon() -> dict:
    from cvm import private

    own = private.json("voice/trained_lexicon.json", {})
    return own if isinstance(own, dict) else {}


def trained_as_written(language: str) -> set[str]:
    extra = (_private_lexicon().get("as_written") or {}).get(language) or []
    return TRAINED_AS_WRITTEN.get(language, set()) | {str(x).lower() for x in extra}


def trained_say(language: str) -> dict[str, str]:
    return {**TRAINED_SAY.get(language, {}), **((_private_lexicon().get("say") or {}).get(language) or {})}


def _trained_lexicon(lexicon: dict[str, str] | None, language: str) -> dict[str, str]:
    keep = trained_as_written(language) | {k.lower() for k in trained_say(language)}
    out = {}
    for k, v in (lexicon or {}).items():
        if k.lower() in keep:
            continue
        if language == "en-US" and _SYLLABLES.search(v) and "-" not in k and not re.search(r"\d", k):
            continue
        out[k] = v
    return out
TRAINED_SAY: dict[str, dict[str, str]] = {}  # picked by ear for one voice: private/voice/trained_lexicon.json


def prepare(text: str, language: str, lexicon: dict[str, str] | None = None, trained: bool = False) -> str:
    """Script text → what the TTS reads. Dates, times, money, versions and tech units are spelled before generic
    numbers, and numbers before acronyms (so "US$", "AED" and "GB" next to a number are not read as letters).
    `trained=True` for a voice fine-tuned on the user's own reading: English names/loanwords stay as written
    (trained_as_written, no LOANWORDS) and trained_say applies."""
    if trained:
        lexicon = _trained_lexicon(lexicon, language)
        text = apply_pronunciation(apply_pronunciation(text, lexicon), trained_say(language))
    else:
        text = apply_loanwords(apply_pronunciation(text, lexicon), language)
    if language == "en-US":
        text = contract_english(text)
    text = spell_tech(text, language)
    for ab, full in SAY_ABBREV.get(language, {}).items():
        text = re.sub(rf"(?<!\w){re.escape(ab)}\.(?=\s|$)", full, text)
    text = spell_numbers(text, language)
    return re.sub(r"\s+", " ", spell_acronyms(text, language, bare_letters=trained and language == "en-US")).strip()


def split_sentences(text: str, language: str) -> list[str]:
    """Sentence chunks for TTS. Keeps abbreviations intact, splits overlong sentences at commas, and merges
    fragments shorter than MIN_WORDS into the previous chunk."""
    protected = text
    for ab in _ABBREV.get(language, []):
        protected = re.sub(rf"\b{re.escape(ab)}\.", ab + "․", protected)
    parts = re.split(r"(?<=[.!?…؟])[\"'”»)]*\s+", protected)
    out: list[str] = []
    for p in (x.replace("․", ".").strip() for x in parts):
        if not p:
            continue
        pieces = [p]
        if len(p) > MAX_CHARS:  # split at clause punctuation, packing clauses up to MAX_CHARS
            pieces, cur = [], ""
            for clause in re.split(r"(?<=[,;:،؛])\s+", p):
                if cur and len(cur) + len(clause) + 1 > MAX_CHARS:
                    pieces.append(cur)
                    cur = clause
                else:
                    cur = f"{cur} {clause}".strip()
            pieces.append(cur)
        for piece in pieces:
            if out and len(piece.split()) < MIN_WORDS:
                out[-1] = f"{out[-1]} {piece}"
            else:
                out.append(piece)
    if len(out) > 1 and len(out[0].split()) < MIN_WORDS:
        out[1] = f"{out[0]} {out[1]}"
        out.pop(0)
    return out
