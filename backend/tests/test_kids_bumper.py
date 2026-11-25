import pytest

from cvm.models import Channel
from cvm.render.bumper import GenericBumper, texts


def test_kids_bumper_never_generic():
    bare = Channel(id=5, name="Canal Exemplo Kids", platform="youtube", language="pt-BR", made_for_kids=True)
    with pytest.raises(GenericBumper):
        texts(bare, "pt-BR")
    cacau = {"bumper": {"pt-BR": {"say": "Olá, exploradores!"}}, "bumper_speaker": {"channel": 3, "name": "Cacau"}}
    set_up = Channel(id=5, name="Canal Exemplo Kids", platform="youtube", language="pt-BR", made_for_kids=True, prefs=cacau)
    assert texts(set_up, "pt-BR")["say"] == "Olá, exploradores!"
    with pytest.raises(GenericBumper):  # a language without its own line
        texts(set_up, "en-US")
    grown_up = Channel(id=1, name="Studio", platform="youtube", language="pt-BR")
    assert texts(grown_up, "pt-BR")["say"]  # other channels keep the default line
