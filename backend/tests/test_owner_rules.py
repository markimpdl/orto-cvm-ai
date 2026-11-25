"""Owner-only rules live in private/ (absent in the public copy) and the code carries no personal names."""

import json
from pathlib import Path

import pytest

from cvm import private
from cvm.config import REPO_ROOT, Settings, home_tz, settings, system_tz
from cvm.public_terms import find_terms, load_terms

TERMS = REPO_ROOT / "scripts" / "public" / "terms.txt"
# LICENSE/README carry the owner's name where it is allowed; scripts/export_public.py checks them in the built tree
EXPORT_EXCLUDED = {"scripts/public/terms.txt", "scripts/public/LICENSE", "scripts/public/README.md",
                   "scripts/update_bundle.py", "scripts/requeue_editorial.py"}


def _scanned() -> list[Path]:
    scripts = [p for p in (REPO_ROOT / "scripts").rglob("*") if p.is_file() and "__pycache__" not in p.parts
               and p.relative_to(REPO_ROOT).as_posix() not in EXPORT_EXCLUDED
               and not p.name.startswith("migrate_channels_")]
    # backend/tests: the owner's real scripts sit in private/fixtures (read by the tests when present)
    return [REPO_ROOT / "backend" / "cvm", REPO_ROOT / "backend" / "tests", REPO_ROOT / "frontend",
            REPO_ROOT / ".claude" / "agents", REPO_ROOT / ".claude" / "skills", *scripts]


def test_no_personal_terms_in_code():
    if not TERMS.exists():
        pytest.skip("no scripts/public/terms.txt (public copy)")
    assert find_terms(_scanned(), load_terms(TERMS)) == []


def test_find_terms_reports_text_and_file_names(tmp_path):
    (tmp_path / "ok.py").write_text("nothing here\n")
    (tmp_path / "a.md").write_text("line one\nhello FOObar\n")
    (tmp_path / "foo_notes.txt").write_text("x\n")
    (tmp_path / "bin.dat").write_bytes(b"\0foo")
    hits = find_terms([tmp_path], ["foo"])
    assert sorted(hits) == sorted([f"{tmp_path / 'a.md'}:2: foo", f"{tmp_path / 'foo_notes.txt'}:0: foo"])


def test_find_terms_folds_accents_and_case_and_whole_words(tmp_path):
    (tmp_path / "a.txt").write_text("A série do CORAÇÃO\nas ações\nruido e Rui.\nbanana\n", encoding="utf-8")
    hits = find_terms([tmp_path], ["Coracao", "Acoes", "~Rui", "~Ana"])  # "~" = whole words: not "ruido", "banana"
    assert sorted(h.split(": ", 1)[1] + "@" + h.rsplit(":", 2)[-2] for h in hits) == ["Acoes@2", "Coracao@1", "~Rui@3"]
    (tmp_path / "Coração.md").write_text("x", encoding="utf-8")
    assert any(h.endswith("Coração.md:0: coracao") for h in find_terms([tmp_path], ["coracao"]))


def _owner(name: str) -> Path:
    f = private.PRIVATE_DIR / name
    if not f.exists():
        pytest.skip(f"no private/{name} (public copy)")
    return f


def test_owner_rules_load_from_private(monkeypatch, tmp_path):
    from cvm.grow import discover
    from cvm.voice import finetune, studio

    own = json.loads(_owner("prompts/discover.json").read_text(encoding="utf-8"))
    assert discover.prompts() == own  # the owner's topic lines, verbatim
    reading = _owner("voice/reading/estudio_pt-BR.txt").read_text(encoding="utf-8")
    assert [s["text"] for s in studio.sentences("pt-BR")] == \
        [x.strip() for x in reading.splitlines() if x.strip() and not x.strip().startswith("# ")]
    assert finetune.default_name() == json.loads(_owner("voice/finetune.json").read_text())["name"]

    # without private/ (the public copy): the generic text, same sentence count (takes are matched by index)
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    assert discover.prompts() == {"editorial": discover.EDITORIAL, "third": discover.THIRD,
                                  "video_angle": discover.VIDEO_ANGLE}
    public = studio.sentences("pt-BR")
    assert len(public) == len([x for x in reading.splitlines() if x.strip() and not x.strip().startswith("# ")])
    assert finetune.default_name() == "voz-real"
    generic = "\n".join([*discover.prompts().values(), *(s["text"] for s in public)])
    (tmp_path / "generic.txt").write_text(generic, encoding="utf-8")
    assert find_terms([tmp_path / "generic.txt"], load_terms(TERMS)) == []



@pytest.mark.parametrize("lang", ["pt-BR", "en-US"])
def test_reading_script_owner_vs_generic(monkeypatch, tmp_path, lang):
    """The owner's takes are matched to his reading script by index: with private/ the studio serves his text verbatim;
    without it, a neutral script (no life story in one city) with the same sections and sentence count."""
    from cvm.voice import studio

    def parse(text: str) -> list[tuple[str, str]]:
        out, section = [], ""
        for line in (x.strip() for x in text.splitlines()):
            if line.startswith("# "):
                section = line[2:].strip()
            elif line:
                out.append((section, line))
        return out

    own = parse(_owner(f"voice/reading/estudio_{lang}.txt").read_text(encoding="utf-8"))
    words = [w.strip() for w in _owner("voice/reading/owner_words.txt").read_text(encoding="utf-8").splitlines()
             if w.strip() and not w.startswith("#")]
    assert [(s["section"], s["text"]) for s in studio.sentences(lang)] == own
    monkeypatch.setattr(private, "PRIVATE_DIR", tmp_path)
    generic = [(s["section"], s["text"]) for s in studio.sentences(lang)]
    assert [sec for sec, _ in generic] == [sec for sec, _ in own]  # same blocks, same sizes
    text = " ".join(t for _, t in generic).lower()
    assert words and not any(w.lower() in text for w in words)

def test_default_tz_from_env(monkeypatch):
    monkeypatch.setenv("CVM_DEFAULT_TZ", "Asia/Dubai")
    assert Settings().default_tz == "Asia/Dubai"
    monkeypatch.setenv("CVM_DEFAULT_TZ", "Not/AZone")
    assert Settings().default_tz is None  # invalid → the system's
    monkeypatch.delenv("CVM_DEFAULT_TZ")
    monkeypatch.setattr(settings, "default_tz", "America/Sao_Paulo")
    assert home_tz().key == "America/Sao_Paulo"
    monkeypatch.setattr(settings, "default_tz", None)
    assert home_tz().key == system_tz()
    monkeypatch.setenv("TZ", "America/New_York")
    assert system_tz() == "America/New_York"


def test_locale_endpoint_reports_the_studio_tz(monkeypatch):
    from fastapi.testclient import TestClient

    from cvm.main import app

    monkeypatch.setattr(settings, "default_tz", "America/Sao_Paulo")
    assert TestClient(app).get("/system/locale").json() == {"tz": "America/Sao_Paulo"}
