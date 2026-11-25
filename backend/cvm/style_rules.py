"""Rules per video type written in Configurações > Regras por tipo (Setting "style_rules"):
{style: {thumbnail, title, hook, ending, bumper}}. The scriptwriter and the SEO agent get the project's style rules
through get_project (`style_rules`) and apply them on top of the style skill. Skill files are never edited from the UI.
"""

from sqlmodel import Session

from cvm.models import Setting, Style

KEY = "style_rules"
FIELDS = ("thumbnail", "title", "hook", "ending", "bumper")
MAX_LEN = 2000


def get_all(session: Session) -> dict[str, dict[str, str]]:
    row = session.get(Setting, KEY)
    saved = (row.value if row else None) or {}
    return {st.value: {f: str((saved.get(st.value) or {}).get(f) or "") for f in FIELDS} for st in Style}


def for_style(session: Session, style: str | None) -> dict[str, str] | None:
    """The non-empty rules of one style, or None when there are none."""
    if not style:
        return None
    rules = {k: v.strip() for k, v in get_all(session).get(str(style), {}).items() if v and v.strip()}
    return rules or None


def put(session: Session, data: dict) -> dict[str, dict[str, str]]:
    """Merge {style: {field: text}}; unknown styles/fields raise ValueError."""
    current = get_all(session)
    for style, fields in (data or {}).items():
        if style not in current or not isinstance(fields, dict):
            raise ValueError(f"tipo desconhecido: {style!r}")
        for f, text in fields.items():
            if f not in FIELDS:
                raise ValueError(f"campo desconhecido: {f!r}")
            text = str(text or "").strip()
            if len(text) > MAX_LEN:
                raise ValueError(f"{style}.{f}: texto longo demais (máx. {MAX_LEN})")
            current[style][f] = text
    row = session.get(Setting, KEY) or Setting(key=KEY)
    row.value = {st: {f: t for f, t in fs.items() if t} for st, fs in current.items() if any(fs.values())}
    session.add(row)
    session.commit()
    return current
