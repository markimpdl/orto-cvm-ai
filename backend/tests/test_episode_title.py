from sqlmodel import Session, SQLModel, create_engine

from cvm.models import Project
from cvm.pipeline import name_episode


def _session():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    return Session(eng)


def test_untitled_episode_takes_the_script_title():
    with _session() as s:
        p = Project(title="Linha do Tempo – Ep. 2", topic="x")
        s.add(p); s.commit(); s.refresh(p)
        assert name_episode(s, p.id, {"packaging": {"title": "Como nasceu o relógio?"}})
        assert p.title == "Linha do Tempo – Ep. 2: Como nasceu o relógio?"
        # named already: a later version or language does not rename it
        assert not name_episode(s, p.id, {"packaging": {"title": "Outro"}})
        assert p.title.endswith("o relógio?")


def test_regular_videos_and_scripts_without_title_untouched():
    with _session() as s:
        p = Project(title="GTA6: Como a série começou", topic="x")
        q = Project(title="Dino Curioso – Ep. 3", topic="x")
        s.add(p); s.add(q); s.commit()
        assert not name_episode(s, p.id, {"packaging": {"title": "Novo"}})
        assert not name_episode(s, q.id, {"hook": "oi"})
        assert p.title == "GTA6: Como a série começou" and q.title == "Dino Curioso – Ep. 3"


def test_title_that_already_carries_the_episode_prefix_is_not_doubled():
    with _session() as s:
        p = Project(title="Linha do Tempo – Ep. 3", topic="x")
        s.add(p); s.commit(); s.refresh(p)
        assert name_episode(s, p.id, {"packaging": {"title": "Linha do Tempo – Ep. 3: Como nasceu o PAPEL?"}})
        assert p.title == "Linha do Tempo – Ep. 3: Como nasceu o PAPEL?"


def test_series_name_and_number_not_repeated():
    from cvm.pipeline import episode_label

    assert episode_label("Canal Exemplo — Amigos do Cacau – Ep. 7: Por que o céu é azul?") == "Por que o céu é azul?"
    assert episode_label("VULCÃO DE BICARBONATO que transbordou! | Dino Curioso Ep. 1") == "VULCÃO DE BICARBONATO que transbordou!"
    assert episode_label("Para onde vai a chuva? – Amigos do Cacau Ep. 8") == "Para onde vai a chuva?"
    assert episode_label("O barquinho do Gil sumiu?! | Amigos do Cacau", "Amigos do Cacau ") == "O barquinho do Gil sumiu?!"
    assert episode_label("أصدقاء الغابة – الحلقة 6: وين راحت الكرة؟") == "وين راحت الكرة؟"
    assert episode_label("Como nasceu o PAPEL? A folha que mudou tudo") == "Como nasceu o PAPEL? A folha que mudou tudo"
