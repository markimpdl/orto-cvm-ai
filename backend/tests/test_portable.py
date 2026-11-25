import sqlite3

from cvm.portable import rewrite_paths


def test_rewrite_paths_plain_and_json(tmp_path):
    db = tmp_path / "x.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table asset (id integer, path varchar, meta json, n integer)")
    con.execute("insert into asset values (1, '/old/root/data/a.png', '{\"p\": \"/old/root/data/b.wav\"}', 3)")
    con.commit()
    con.close()
    assert rewrite_paths(db, "/old/root", "/new/place") == 2
    row = sqlite3.connect(db).execute("select path, meta, n from asset").fetchone()
    assert row == ("/new/place/data/a.png", '{"p": "/new/place/data/b.wav"}', 3)
