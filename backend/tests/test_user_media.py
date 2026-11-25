from cvm.visuals.select import _apply_media_mode


def scene(i, type_, **v):
    return {"index": i, "narration": "x", "visual": {"type": type_, **v}}


def test_mine_mode_fills_every_scene_with_user_files_round_robin():
    used: list[int] = []
    out = [_apply_media_mode(s, "mine", [10, 11], used)["visual"] for s in
           (scene(0, "broll"), scene(1, "user_media", media_id=11), scene(2, "ai_image"), scene(3, "avatar_full"), scene(4, "chart"))]
    assert [(v["type"], v.get("media_id")) for v in out] == [
        ("user_media", 10), ("user_media", 11), ("user_media", 10), ("avatar_full", 11), ("chart", None)]  # the presenter speaks over the user's file


def test_online_mode_drops_user_files_and_mix_keeps_the_script():
    v = _apply_media_mode(scene(0, "user_media", media_id=3, search_terms=["praia"]), "online", [], [])["visual"]
    assert v["type"] == "broll"
    s = scene(1, "broll")
    assert _apply_media_mode(s, "mix", [3], []) is s


def test_upload_and_use_on_scene(tmp_path, monkeypatch):
    import io

    from fastapi import UploadFile
    from sqlmodel import Session, SQLModel, create_engine, select

    from cvm.api import media
    from cvm.config import settings
    from cvm.models import Asset, Project

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    s = Session(engine)
    p = Project(title="t", topic="t")
    s.add(p)
    s.commit()
    a = media.save_media(s, p.id, UploadFile(io.BytesIO(b"\x89PNG fake"), filename="minha foto.png"), "eu na praia")
    assert a.meta["kind"] == "image" and a.meta["description"] == "eu na praia"
    assert media.listing(s, p.id)[0]["file_url"] == f"/assets/{a.id}/file"
    v = media.use_on_scene(s, p.id, 2, a)
    assert v.path == a.path and v.meta["media_id"] == a.id
    assert s.exec(select(Asset).where(Asset.kind == "visual")).one().scene == 2
