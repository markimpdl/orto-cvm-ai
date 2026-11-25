"""Real subjects are never drawn outside kids episodes (safari 643: a flat-vector sand cat, cartoon rhinos)."""
from cvm.visuals.select import photographic


def scene(prompt, kids=False):
    v = {"type": "ai_image", "image_prompt": prompt}
    if kids:
        v["_kids"] = True
    return {"index": 0, "visual": v}


def test_style_words_removed_subject_kept():
    s, drawn = photographic(scene("Flat vector illustration of a small sand cat with big ears sitting on a dune"))
    assert drawn
    p = s["visual"]["image_prompt"]
    assert "sand cat" in p and "dune" in p and "vector" not in p.lower() and "illustration" not in p.lower()
    assert "photograph" in p


def test_photo_prompt_untouched():
    s, drawn = photographic(scene("aerial photo of Lisbon skyline at dusk"))
    assert not drawn and s["visual"]["image_prompt"] == "aerial photo of Lisbon skyline at dusk"


def test_kids_keep_their_drawings():
    s, drawn = photographic(scene("cartoon fox in a forest", kids=True))
    assert not drawn and s["visual"]["image_prompt"] == "cartoon fox in a forest"
