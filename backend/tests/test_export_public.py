"""scripts/export_public.py: clean public tree, personal-terms guard, release into a local clone (tmp repos only)."""

import importlib.util
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_public.py"
AUTHOR = ("Test Owner", "owner@example.com")


def load():
    spec = importlib.util.spec_from_file_location("export_public", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path, monkeypatch):
    """No global/system git config (signing, hooks, default branch) leaks into the tmp repos."""
    empty = tmp_path / "gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, rel: str, text: str = "x\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    files = {
        "backend/app.py": "print('ok')\n",
        "README.md": "owner readme\n",
        "CLAUDE.md": "owner notes\n",
        "HANDOFF.md": "h\n",
        "docs/plan.md": "plan\n",
        "bakeoff/run.py": "b\n",
        "private/rules.md": "secret rules\n",
        ".claude/settings.json": "{}\n",
        ".claude/settings.local.json": "{}\n",
        ".claude/agents/writer.md": "agent\n",
        ".impeccable/config.json": "{}\n",
        "scripts/migrate_channels_2026_11.py": "m\n",
        "scripts/update_bundle.py": "u\n",
        "scripts/requeue_editorial.py": "r\n",
        "scripts/run.sh": "#!/bin/sh\necho hi\n",
        "scripts/public/README.md": "public readme\n",
        "scripts/public/LICENSE": "license\nRequired Notice: Copyright Test Owner\n",
        "scripts/public/CLAUDE.md": "public notes\n",
        "scripts/public/.env.example": "CVM_VIDEO_LANGUAGES=pt-BR,en-US\n",
        "scripts/public/terms.txt": "Zorblax\n",
    }
    for rel, text in files.items():
        write(repo, rel, text)
    (repo / "scripts/run.sh").chmod(0o755)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    write(repo, "untracked.txt")
    return repo


def make_clone(tmp: Path) -> tuple[Path, Path]:
    bare = tmp / "orto-cvm-ai.git"  # release() pushes only to a remote with this name
    git(tmp, "init", "-q", "--bare", str(bare))
    clone = tmp / "clone"
    git(tmp, "clone", "-q", str(bare), str(clone))
    return bare, clone


def test_build_excludes_and_overlays(tmp_path):
    ep = load()
    repo = make_repo(tmp_path)
    out = tmp_path / "out"
    copied = ep.build_tree(repo, out)
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert files == sorted(copied)
    assert files == sorted([".claude/agents/writer.md", ".claude/settings.json", ".env.example", "CLAUDE.md",
                            "LICENSE", "README.md", "backend/app.py", "scripts/run.sh"])
    assert (out / "README.md").read_text() == "public readme\n"
    assert (out / "CLAUDE.md").read_text() == "public notes\n"
    assert os.access(out / "scripts/run.sh", os.X_OK)


def test_guard_scans_json_and_filenames(tmp_path):
    ep = load()
    out = tmp_path / "out"
    write(out, "data/cfg.json", json.dumps({"title": "Canal do zorblax"}, indent=1))
    write(out, "assets/zorblax_logo.txt")
    write(out, "zorblax-dir/a.txt")
    home = "/Users/" + "realperson"  # split so this file passes the guard itself
    write(out, "src/paths.py", f"HOME = '{home}/projects'\nGENERIC = '/Users/someone/x'\nBARE = '/Users/'\n")
    write(out, "src/keys.py", "K = 'ghp_" + "a" * 36 + "'\nG = 'AIza" + "B" * 35 + "'\nS = 'sk-" + "c" * 40 + "'\n")
    write(out, "src/mail.py", "A = 'me" + "@realmail.io'\nB = 'you@example.com'\n")
    write(out, "src/clean.py", "print('nothing personal')\n")
    win = "C:" + "\\Users\\" + "realperson"
    write(out, "src/other_os.py", "L = '/home/" + "realperson/x'\n" + f"W = r'{win}\\x'\nG = '/home/user/x'\n")
    with zipfile.ZipFile(out / "assets/pack.zip", "w") as z:
        z.writestr("voice/zorblax_ref.wav", b"RIFF")
        z.writestr("avatar.json", '{"name": "Zorblax"}')
        z.writestr("ok.txt", "fine")
    hits = ep.scan(out, ["Zorblax"])
    assert "assets/pack.zip!voice/zorblax_ref.wav:0: Zorblax" in hits
    assert "assets/pack.zip!avatar.json: Zorblax" in hits
    assert not any("ok.txt" in h for h in hits)
    assert any(h.startswith("src/other_os.py:1:") for h in hits) and any(h.startswith("src/other_os.py:2:") for h in hits)
    assert not any(h.startswith("src/other_os.py:3:") for h in hits)
    assert ep.binaries(out) == ["assets/pack.zip (0.0 MB)"]
    assert "data/cfg.json:2: Zorblax" in hits
    assert "assets/zorblax_logo.txt:0: Zorblax" in hits
    assert any(h.startswith("zorblax-dir/a.txt:0:") for h in hits)
    assert any(h.startswith("src/paths.py:1:") for h in hits)
    assert not any(h.startswith(("src/paths.py:2:", "src/paths.py:3:")) for h in hits)
    assert sum(h.startswith("src/keys.py:") for h in hits) == 3
    assert any(h.startswith("src/mail.py:1:") for h in hits)
    assert not any(h.startswith(("src/mail.py:2:", "src/clean.py")) for h in hits)


def test_owner_name_allowed_only_in_license_and_readme(tmp_path):
    ep = load()
    out = tmp_path / "out"
    write(out, "LICENSE", "PolyForm\n\nRequired Notice: Copyright Ana Zorblax\n")
    write(out, "README.md", "# App\n\nfeito por Ana Zorblax\n\n## Créditos\n\nCriado por Ana Zorblax.\n\n"
                            "## Credits\n\nMade by Ana Zorblax.\n\n## Depois\n\nAna Zorblax again\n")
    write(out, "backend/app.py", "# Ana Zorblax\n")
    write(out, "docs/LICENSE", "Copyright Ana Zorblax\n")
    hits = ep.scan(out, ["Zorblax"])
    assert sorted(hits) == sorted(["README.md:3: Zorblax", "README.md:15: Zorblax", "backend/app.py:1: Zorblax",
                                   "docs/LICENSE:1: Zorblax"])


def test_refuses_existing_tag_and_dirty_clone(tmp_path, monkeypatch):
    ep = load()
    out = tmp_path / "out"
    write(out, "a.txt", "a\n")
    _, clone = make_clone(tmp_path)
    write(clone, "old.txt", "old\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "old")
    git(clone, "tag", "v1.0")
    with pytest.raises(ep.ExportError, match="v1.0"):
        ep.release(out, clone, "v1.0", yes=True, author=AUTHOR)
    write(clone, "stray.txt")
    with pytest.raises(ep.ExportError, match="alterações"):
        ep.release(out, clone, "v1.1", yes=True, author=AUTHOR)
    (clone / "stray.txt").unlink()
    # answering "no" leaves the clone exactly as it was
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    with pytest.raises(ep.ExportError, match="[Cc]ancelad"):
        ep.release(out, clone, "v1.1", yes=False, author=AUTHOR)
    assert git(clone, "status", "--porcelain") == ""
    assert git(clone, "log", "--format=%s") == "old"
    assert (clone / "old.txt").exists() and not (clone / "a.txt").exists()


def test_release_commits_tags_and_pushes_to_bare_remote(tmp_path):
    ep = load()
    bare, clone = make_clone(tmp_path)
    out = tmp_path / "out"
    write(out, "a.txt", "a\n")
    write(out, "sub/b.txt", "b\n")
    ep.release(out, clone, "v1.0", yes=True, author=AUTHOR)
    assert git(bare, "log", "main", "--format=%s|%an|%ae") == f"Release v1.0|{AUTHOR[0]}|{AUTHOR[1]}"
    assert git(bare, "tag", "-l") == "v1.0"
    assert git(bare, "ls-tree", "-r", "--name-only", "main").split() == ["a.txt", "sub/b.txt"]
    # next version: removed files disappear, changed files update
    (out / "sub/b.txt").unlink()
    write(out, "a.txt", "a2\n")
    ep.release(out, clone, "v1.1", yes=True, author=AUTHOR)
    assert git(bare, "ls-tree", "-r", "--name-only", "main").split() == ["a.txt"]
    assert git(bare, "show", "main:a.txt") == "a2"
    assert git(bare, "tag", "-l").split() == ["v1.0", "v1.1"]
    assert git(bare, "log", "main", "--format=%s").split("\n") == ["Release v1.1", "Release v1.0"]


def test_dry_run_changes_nothing(tmp_path, capsys):
    ep = load()
    repo = make_repo(tmp_path)
    bare, clone = make_clone(tmp_path)
    write(clone, "old.txt", "old\n")
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "old")
    git(clone, "push", "-q", "origin", "HEAD:main")
    before = (git(clone, "rev-parse", "HEAD"), git(bare, "rev-parse", "main"))
    out = tmp_path / "tree"
    code = ep.main(["--version", "v1.0", "--clone", str(clone), "--dry-run", "--repo", str(repo), "--out", str(out)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "0 termos" in printed and "novos: 8" in printed and "removidos: 1" in printed
    assert (git(clone, "rev-parse", "HEAD"), git(bare, "rev-parse", "main")) == before
    assert git(clone, "status", "--porcelain") == "" and git(clone, "tag", "-l") == ""
    # a planted term stops the export before the clone is touched
    write(repo, "backend/app.py", "# by Zorblax\n")
    git(repo, "commit", "-qam", "plant")
    code = ep.main(["--version", "v1.0", "--clone", str(clone), "--repo", str(repo), "--out", str(tmp_path / "t2"),
                    "--yes"])
    assert code == 1
    assert "backend/app.py:1: Zorblax" in capsys.readouterr().out
    assert (git(clone, "rev-parse", "HEAD"), git(bare, "rev-parse", "main")) == before


def _released(tmp_path):
    ep = load()
    bare, clone = make_clone(tmp_path)
    out = tmp_path / "out"
    write(out, "a.txt", "a\n")
    ep.release(out, clone, "v1.0", yes=True, author=AUTHOR)
    write(out, "b.txt", "b\n")
    return ep, bare, clone, out


def test_failed_commit_rolls_the_clone_back(tmp_path, monkeypatch):
    ep, bare, clone, out = _released(tmp_path)
    head = git(clone, "rev-parse", "HEAD")
    hook = clone / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(ep.ExportError, match="commit"):
        ep.release(out, clone, "v1.1", yes=True, author=AUTHOR)
    assert git(clone, "rev-parse", "HEAD") == head and git(clone, "tag", "-l") == "v1.0"
    assert git(clone, "status", "--porcelain") == "" and not (clone / "b.txt").exists()
    hook.unlink()
    # a failed tag also undoes the commit
    real = ep._git

    def no_tag(cwd, *args, check=True):
        if "tag" in args and "-a" in args:
            raise ep.ExportError("tag falhou")
        return real(cwd, *args, check=check)

    monkeypatch.setattr(ep, "_git", no_tag)
    with pytest.raises(ep.ExportError, match="tag falhou"):
        ep.release(out, clone, "v1.1", yes=True, author=AUTHOR)
    monkeypatch.setattr(ep, "_git", real)
    assert git(clone, "rev-parse", "HEAD") == head and git(clone, "tag", "-l") == "v1.0"
    assert git(clone, "status", "--porcelain") == ""


def test_failed_push_keeps_commit_and_tag_and_says_how_to_push(tmp_path):
    ep, bare, clone, out = _released(tmp_path)
    hook = bare / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(ep.ExportError) as e:
        ep.release(out, clone, "v1.1", yes=True, author=AUTHOR)
    assert f"git -C {clone} push origin HEAD:refs/heads/main refs/tags/v1.1" in str(e.value)
    assert git(clone, "log", "-1", "--format=%s") == "Release v1.1" and "v1.1" in git(clone, "tag", "-l")
    assert git(bare, "tag", "-l") == "v1.0"


def test_refuses_a_clone_of_another_repo(tmp_path):
    ep = load()
    other = tmp_path / "ortocvm.git"
    git(tmp_path, "init", "-q", "--bare", str(other))
    clone = tmp_path / "c"
    git(tmp_path, "clone", "-q", str(other), str(clone))
    out = tmp_path / "out"
    write(out, "a.txt")
    with pytest.raises(ep.ExportError, match="origin"):
        ep.release(out, clone, "v1.0", yes=True, author=AUTHOR)
    assert git(clone, "status", "--porcelain") == ""


def test_ignored_but_shipped_file_is_committed(tmp_path):
    ep, bare, clone, out = _released(tmp_path)
    write(out, ".gitignore", "*.log\n")
    write(out, "keep.log", "shipped on purpose\n")
    ep.release(out, clone, "v1.1", yes=True, author=AUTHOR)
    assert "keep.log" in git(bare, "ls-tree", "-r", "--name-only", "main").split()


def test_author_is_the_clone_local_identity_only(tmp_path, monkeypatch):
    """The public history is signed with the clone's own identity, never the global or this repo's address."""
    ex = load()
    clone = tmp_path / "orto-cvm-ai"
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.cfg"))
    (tmp_path / "global.cfg").write_text("[user]\n\tname = Global\n\temail = global@example.org\n")
    with pytest.raises(ex.ExportError):
        ex._author(clone)  # only a global identity: refused
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "Dono"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "dono@example.com"], check=True)
    assert ex._author(clone) == ("Dono", "dono@example.com")
