import pytest
from pydantic import ValidationError

from cvm.editorial_text import norm
from cvm.scripting.schema import Editorial, EditorialScene, Proof, ScriptContent, editorial_problems

SOURCES = ["https://xda-developers.com/gta6-pc", "https://example.com/b"]


def _convert(scene: dict) -> dict:
    keep = {k: v for k, v in scene.items() if k not in {"img", "src", "de", "titulo_tela", "selo", "escuro"}}
    if scene["tipo"] == "prova":
        keep["prova"] = {"url": SOURCES[0], "frase": scene.get("texto") or "frase da página",
                         "destaque_em": scene.get("destaque_em"), "traducao": scene.get("traducao")}
        for k in ("destaque_em", "traducao"):
            keep.pop(k, None)
    if scene["tipo"] == "video":
        keep["busca"] = {"termos": ["gta 6 trailer"], "preferir": "oficial"}
    return keep


def _content(cenas: list[dict], narration: str = "", sources=SOURCES) -> dict:
    return {
        "hook": "x",
        "scenes": [{"index": 0, "narration": narration,
                    "visual": {"type": "headline"}}],
        "sources": sources,
        "editorial": {"cenas": cenas},
    }


@pytest.fixture
def ed(editorial_set):
    """The fixture set's scenes converted to the script shape, and a `content(cenas, ...)` over its narration."""
    class Ed:
        cenas = staticmethod(lambda: [_convert(s) for s in editorial_set.cenas])
        narration = editorial_set.narration

        @staticmethod
        def content(cenas, narration=None, sources=SOURCES):
            return _content(cenas, editorial_set.narration if narration is None else narration, sources)
    return Ed


def test_editorial_optional_old_scripts_validate():
    c = ScriptContent.model_validate({"hook": "x", "scenes": [{"index": 0, "narration": "a", "visual": {"type": "headline"}}]})
    assert c.editorial is None


def test_editorial_parses_reference(ed, editorial_set):
    cenas = ed.cenas()
    parsed = Editorial.model_validate({"cenas": cenas})
    assert len(parsed.cenas) == len(editorial_set.cenas) >= 8
    c = ScriptContent.model_validate(ed.content(cenas))
    assert c.editorial and isinstance(c.editorial.cenas[0], EditorialScene)
    assert editorial_problems(ed.content(cenas), "pt-BR") == []


def test_editorial_min_scenes_and_search_terms(ed):
    with pytest.raises(ValidationError):
        Editorial.model_validate({"cenas": ed.cenas()[:7]})
    with pytest.raises(ValidationError):
        EditorialScene.model_validate({"em": "a", "tipo": "video", "busca": {"termos": []}})


def test_problems_anchor_order(ed):
    cenas = ed.cenas()[:8]
    cenas[2]["em"] = "zzzinexistente"
    probs = editorial_problems(ed.content(cenas), "pt-BR")
    assert any("âncora" in p and "zzzinexistente" in p for p in probs)
    # out of order: the second anchor is spoken before the first
    out = [{"em": "ultima", "tipo": "impacto", "texto": "x"}, {"em": "primeira", "tipo": "impacto", "texto": "x"}] * 4
    assert editorial_problems(_content(out, "primeira meio ultima"), "pt-BR")


def test_anchor_numbers_match_spelled():
    cenas = [{"em": a, "tipo": "impacto", "texto": "x"} for a in ["seis", "6", "six", "SEIS,"] * 2]
    assert editorial_problems(_content(cenas, "6 seis six 6 seis six 6 seis"), "pt-BR") == []
    assert norm("Seis,") == norm("6") == norm("six")


def test_problems_proof_url_not_in_sources(ed):
    cenas = ed.cenas()
    probs = editorial_problems(ed.content(cenas, sources=["https://other.com"]), "pt-BR")
    assert any("sources" in p for p in probs)


def test_problems_text_limits(ed):
    base = {"em": "No", "tipo": "impacto", "texto": "x" * 61}
    cenas = [base] + ed.cenas()[1:]
    assert any("60" in p for p in editorial_problems(ed.content(cenas), "pt-BR"))
    cenas = [{"em": "No", "tipo": "numero", "valor": "1" * 13}] + ed.cenas()[1:]
    assert any("12" in p for p in editorial_problems(ed.content(cenas), "pt-BR"))
    for itens in ([], [["a", "b"]] * 6):
        cenas = [{"em": "No", "tipo": "lista", "itens": itens}] + ed.cenas()[1:]
        assert any("1 a 5 itens" in p for p in editorial_problems(ed.content(cenas), "pt-BR"))
    cenas = [{"em": "No", "tipo": "lista", "itens": [["a", "b"]]}] + ed.cenas()[1:]
    assert not any("itens" in p for p in editorial_problems(ed.content(cenas), "pt-BR"))


def test_anchors_must_be_one_word():
    for bad in ("dois mil", " ", "..."):
        with pytest.raises(ValidationError, match="única palavra"):
            EditorialScene.model_validate({"em": bad, "tipo": "impacto"})
    with pytest.raises(ValidationError, match="única palavra"):
        EditorialScene.model_validate({"em": "a", "tipo": "lista", "itens": [["dois mil", "x"]]})
    with pytest.raises(ValidationError, match="única palavra"):
        Proof(url="u", frase="f", destaque_em="de graça")


def test_proof_translation_only_when_lang_declared(ed):
    first = ed.cenas()
    def with_proof(**extra):
        p = {"em": "No", "tipo": "prova", "prova": {"url": SOURCES[0], "frase": "f", **extra}}
        return [p] + first[1:]
    def lang_problems(**extra):
        return [p for p in editorial_problems(ed.content(with_proof(**extra)), "pt-BR") if "traducao" in p]
    assert lang_problems() == []
    assert lang_problems(lang="en")
    assert lang_problems(lang="en", traducao="t") == []
    assert lang_problems(lang="pt") == []
    assert Proof(url="u", frase="f").lang is None


def _three(narration: str, second: dict) -> dict:
    cenas = [{"em": "Olha", "tipo": "pergunta", "texto": "x"}, second, {"em": "fim", "tipo": "impacto", "texto": "y"}]
    return _content(cenas, narration=narration)


NARR3 = "Olha isso agora três coisas carro barco avião e pronto fim do vídeo depois lá"


def test_item_anchors_must_be_said_inside_their_scene():
    """Review finding 3: inner anchors fail at save, not later in the visuals step."""
    ok = {"em": "três", "tipo": "lista", "itens": [["três", "Carro"], ["barco", "Barco"], ["avião", "Avião"]]}
    assert editorial_problems(_three(NARR3, ok), "pt-BR") == []
    late = {**ok, "itens": [["três", "Carro"], ["depois", "Depois"]]}  # said only after the next scene's anchor
    probs = editorial_problems(_three(NARR3, late), "pt-BR")
    assert len(probs) == 1 and "depois" in probs[0] and "cena 1" in probs[0]
    early = {**ok, "itens": [["Olha", "Antes"]]}  # said before the scene's own anchor
    assert any("Olha" in p for p in editorial_problems(_three(NARR3, early), "pt-BR"))
    missing = {**ok, "itens": [["zebra", "Z"]]}
    assert any("zebra" in p for p in editorial_problems(_three(NARR3, missing), "pt-BR"))


def test_proof_highlight_and_impact_anchors_checked():
    prova = {"em": "três", "tipo": "prova", "prova": {"url": SOURCES[0], "frase": "f", "destaque_em": "barco"}}
    assert editorial_problems(_three(NARR3, prova), "pt-BR") == []
    bad = {**prova, "prova": {**prova["prova"], "destaque_em": "vídeo"}}
    assert any("destaque_em" in p and "vídeo" in p for p in editorial_problems(_three(NARR3, bad), "pt-BR"))
    imp = {"em": "três", "tipo": "impacto", "texto": "UAU", "impacto_em": "avião"}
    assert EditorialScene.model_validate(imp).impacto_em == "avião"
    assert editorial_problems(_three(NARR3, imp), "pt-BR") == []
    assert any("impacto_em" in p for p in editorial_problems(_three(NARR3, {**imp, "impacto_em": "lá"}), "pt-BR"))
    with pytest.raises(ValidationError):
        EditorialScene.model_validate({**imp, "impacto_em": "duas palavras"})
