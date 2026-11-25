from cvm.publish.platforms import youtube_tags


def test_youtube_tags_cleaned_and_within_budget():
    tags = ["#UAE Flag Day", "uae flag day", "a,b", "<x>", ""] + [f"tag numero {i}" for i in range(60)]
    out = youtube_tags(tags)
    assert out[:3] == ["UAE Flag Day", "a b", "x"]
    assert sum(len(t) + (2 if " " in t else 0) for t in out) + len(out) - 1 <= 500
    assert len({t.lower() for t in out}) == len(out)


def test_category_from_title_and_channel():
    from types import SimpleNamespace as N

    from cvm.publish.platforms import category_for

    ch = N(made_for_kids=False, default_style="react", prefs={"youtube_category": "28"})
    assert category_for(N(seo={"title": "GTA 6: realismo ou diversão?", "tags": []}), ch) == "20"
    assert category_for(N(seo={"title": "iPhone Duo vale a pena?", "tags": []}), ch) == "28"
    assert category_for(N(seo={"title": "Um dia comum", "tags": []}), ch) == "28"  # channel default
    assert category_for(N(seo={"title": "x", "category_id": "26"}), ch) == "26"  # SEO choice wins
    fixed = N(made_for_kids=False, default_style="explainer", prefs={"youtube_category": "29", "youtube_category_fixed": True})
    assert category_for(N(seo={"title": "GTA", "category_id": "20"}), fixed) == "29"
    assert category_for(N(seo={"title": "Ep. 7"}), N(made_for_kids=True, prefs={})) == "1"
