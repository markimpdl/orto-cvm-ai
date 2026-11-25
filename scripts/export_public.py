"""Builds the public copy (orto-cvm-ai) from this repo and releases it into a local clone of the public repository.

    uv run --project backend python scripts/export_public.py --version v1.0 [--clone ../orto-cvm-ai] [--dry-run]

1. Tree: the files tracked by git, minus EXCLUDE, plus the public-only files in scripts/public/ (README.md, LICENSE,
   CLAUDE.md, .env.example) at the root. scripts/public/terms.txt never leaves this repo.
2. Guard: personal terms (scripts/public/terms.txt) in text and file names, e-mails, home paths and token-looking keys.
   Any hit stops the export before the clone is touched. The owner's name is allowed only in LICENSE and in the
   README credits section; his e-mail only in the commit metadata.
3. Summary (new/changed/removed vs the clone), confirmation, commit "Release vX.Y" with this repo's git identity,
   tag, push of `main` + the tag. Never --force. --dry-run stops after the summary.
"""

import argparse
import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from cvm.public_terms import SKIP_DIRS, find_terms, fold, load_terms, matcher  # noqa: E402

PUBLIC = "scripts/public"
OVERLAY = ["README.md", "LICENSE", "CLAUDE.md", ".env.example"]
# Directory prefixes end with "/"; everything else is a glob on the repo-relative path or on the file name.
EXCLUDE = [
    "docs/", "bakeoff/", "private/", "scripts/public/", ".superpowers/", ".worktrees/",
    ".impeccable/",  # detector notes cite a third party's catalogue
    "HANDOFF.md", "handoff.zip", ".claude/settings.local.json",
    "scripts/migrate_channels_*", "scripts/update_bundle.py", "scripts/requeue_editorial.py",
]
EXCLUDE_NAMES = ["CLAUDE.md"]  # the owner's notes, anywhere; the public one is overlaid at the root

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_OK_DOMAINS = {"example.com", "example.org", "example.net", "localhost"}
HOME = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\{1,2}Users\\{1,2})([A-Za-z0-9._-]+)")
HOME_OK = {"someone", "user", "username", "name", "me", "you", "example", "shared", "public", "runner"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".icns", ".wav", ".mp3", ".m4a", ".ogg", ".flac",
              ".mp4", ".mov", ".webm", ".zip", ".pdf", ".ttf", ".otf", ".woff", ".woff2"}
PUBLIC_REMOTE = re.compile(r"orto-cvm-ai(\.git)?/?$")
TOKENS = [re.compile(p) for p in (r"\bsk-[A-Za-z0-9_-]{20,}", r"\bghp_[A-Za-z0-9]{30,}", r"\bgithub_pat_[A-Za-z0-9_]{30,}",
                                  r"\bAIza[0-9A-Za-z_-]{35}", r"\bxox[abp]-[A-Za-z0-9-]{10,}")]
CREDITS = re.compile(r"^#+\s*(créditos|creditos|credits)\b", re.I)
NOTICE = re.compile(r"^Required Notice:\s*Copyright\s+(?:\(c\)\s*)?(?:\d{4}\s+)?(.+?)\s*$", re.I | re.M)
VERSION = re.compile(r"^v\d+\.\d+(\.\d+)?$")


class ExportError(Exception):
    pass


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise ExportError(f"git {' '.join(args)} falhou em {cwd}: {r.stderr.strip() or r.stdout.strip()}")
    return r


def _excluded(rel: str) -> bool:
    if Path(rel).name in EXCLUDE_NAMES:
        return True
    for pat in EXCLUDE:
        if pat.endswith("/"):
            if rel.startswith(pat):
                return True
        elif fnmatch.fnmatchcase(rel, pat):
            return True
    return False


def build_tree(repo: Path, out: Path) -> list[str]:
    """Copies the tracked files of `repo` (working tree content) minus EXCLUDE into `out`, then the public-only files.
    Returns the repo-relative paths written, sorted."""
    repo, out = Path(repo), Path(out)
    if out.exists() and any(out.iterdir()):
        raise ExportError(f"a pasta de saída não está vazia: {out}")
    tracked = _git(repo, "ls-files", "-z").stdout.split("\0")
    written: set[str] = set()
    for rel in filter(None, tracked):
        src = repo / rel
        if _excluded(rel) or not src.is_file():  # deleted but still tracked → skip
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
        written.add(rel)
    for name in OVERLAY:
        src = repo / PUBLIC / name
        if not src.is_file():
            raise ExportError(f"falta {PUBLIC}/{name}")
        shutil.copy2(src, out / name)
        written.add(name)
    return sorted(written)


def _files(out: Path):
    for f in sorted(out.rglob("*")):
        rel = f.relative_to(out)
        if f.is_file() and not SKIP_DIRS.intersection(rel.parts):
            yield f, rel.as_posix()


def _text(f: Path) -> str | None:
    raw = f.read_bytes()
    if b"\0" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _owner_lines(out: Path) -> tuple[str | None, dict[str, set[int]]]:
    """The owner's name (from LICENSE's Required Notice) and the lines where it may appear: LICENSE lines that carry
    it, and any line inside a README credits section."""
    lic = out / "LICENSE"
    m = NOTICE.search(lic.read_text(encoding="utf-8")) if lic.is_file() else None
    if not m:
        return None, {}
    name = m.group(1).strip()
    allowed = {"LICENSE": {n for n, line in enumerate(lic.read_text(encoding="utf-8").splitlines(), 1)
                           if name.lower() in line.lower()}}
    readme = out / "README.md"
    if readme.is_file():
        level, lines = 0, set()
        for n, line in enumerate(readme.read_text(encoding="utf-8").splitlines(), 1):
            head = re.match(r"^(#+)\s", line)
            if head:
                depth = len(head.group(1))
                if CREDITS.match(line):
                    level = depth
                    continue
                if level and depth <= level:
                    level = 0
            if level and name.lower() in line.lower():
                lines.add(n)
        allowed["README.md"] = lines
    return name, allowed


def scan(out: Path, terms: list[str]) -> list[str]:
    """Every problem in the built tree as "relative/path:line: what" (line 0 = the path itself)."""
    out = Path(out)
    hits: list[str] = []
    name, allowed = _owner_lines(out)
    owner_words = {fold(w) for w in (name or "").split()} | ({fold(name)} if name else set())
    prefix = len(str(out)) + 1
    for h in find_terms([out], terms):
        path, line, term = re.match(r"^(.*):(\d+): (.*)$", h).groups()
        rel = path[prefix:]
        if fold(term) in owner_words and int(line) in allowed.get(rel, set()):
            continue
        hits.append(f"{rel}:{line}: {term}")
    found = matcher(terms)
    for f, rel in _files(out):
        for d in Path(rel).parent.parts:  # directory names (find_terms only looks at file names)
            hits += [f"{rel}:0: {t}" for t in found(d)]
        text = _text(f)
        if text is None:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in EMAIL.finditer(line):
                domain = m.group(0).rsplit("@", 1)[1].lower()
                if domain not in EMAIL_OK_DOMAINS and not domain.endswith((".example", ".invalid", ".test")):
                    hits.append(f"{rel}:{n}: e-mail {m.group(0)}")
            for m in HOME.finditer(line):
                if m.group(1).lower() not in HOME_OK:
                    hits.append(f"{rel}:{n}: caminho {m.group(0)}")
            for rx in TOKENS:
                if rx.search(line):
                    hits.append(f"{rel}:{n}: chave {rx.search(line).group(0)[:8]}…")
    for f, rel in _files(out):  # inside zips: member names, and the text of small text members
        if f.suffix.lower() != ".zip":
            continue
        try:
            with zipfile.ZipFile(f) as z:
                for info in z.infolist():
                    hits += [f"{rel}!{info.filename}:0: {t}" for t in found(info.filename)]
                    if info.file_size <= 1_000_000 and info.filename.lower().endswith((".json", ".txt", ".md")):
                        body = z.read(info).decode("utf-8", "replace")
                        hits += [f"{rel}!{info.filename}: {t}" for t in found(body)]
        except zipfile.BadZipFile:
            hits.append(f"{rel}:0: zip ilegível")
    return sorted(set(hits), key=hits.index)


def binaries(out: Path) -> list[str]:
    """Shipped images/audio/video/archives/fonts, for the owner to eyeball before a release."""
    return [f"{rel} ({f.stat().st_size / 1e6:.1f} MB)" for f, rel in _files(Path(out))
            if f.suffix.lower() in BINARY_EXT]


def _digest(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def summary(out: Path, clone: Path) -> dict[str, list[str]]:
    """new / changed / removed files of the tree vs the clone's committed tree (read-only)."""
    out, clone = Path(out), Path(clone)
    have = {rel for _, rel in _files(out)}
    old = set(filter(None, _git(clone, "ls-files", "-z").stdout.split("\0"))) if (clone / ".git").exists() else set()
    changed = sorted(r for r in have & old if not (clone / r).is_file() or _digest(out / r) != _digest(clone / r))
    return {"novos": sorted(have - old), "alterados": changed, "removidos": sorted(old - have)}


def _print_summary(s: dict[str, list[str]], limit: int = 15) -> None:
    print("Resumo: " + ", ".join(f"{k}: {len(v)}" for k, v in s.items()))
    for k, v in s.items():
        for rel in v[:limit]:
            print(f"  {k[0].upper()} {rel}")
        if len(v) > limit:
            print(f"  … mais {len(v) - limit} {k}")


def _author(repo: Path = ROOT) -> tuple[str, str]:
    """The release author: the PUBLIC clone's own (`--local`) git identity, so the public history never carries
    the address configured for this private repo or globally."""
    name = _git(repo, "config", "--local", "user.name", check=False).stdout.strip()
    email = _git(repo, "config", "--local", "user.email", check=False).stdout.strip()
    if not name or not email:
        raise ExportError(f"configure git user.name e user.email no clone público ({repo.name}) antes de enviar: "
                          "git -C <clone> config user.email <e-mail>")
    return name, email


def _restore(clone: Path) -> None:
    """Back to the clone's last commit (it was clean before the sync)."""
    if _git(clone, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0:
        _git(clone, "reset", "-q", "--hard")
        _git(clone, "clean", "-fdq")
    else:
        _git(clone, "rm", "-rqf", "--cached", "--ignore-unmatch", ".")
        for p in clone.iterdir():
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()


def release(out: Path, clone: Path, version: str, yes: bool, author: tuple[str, str] | None = None) -> None:
    """Syncs the tree into the clone, asks, commits "Release <version>", tags and pushes main + the tag (no force)."""
    out, clone = Path(out), Path(clone)
    if not VERSION.match(version):
        raise ExportError(f"versão inválida: {version} (use vX.Y)")
    if not (clone / ".git").exists():
        raise ExportError(f"{clone} não é um clone git do repositório público")
    if _git(clone, "status", "--porcelain").stdout.strip():
        raise ExportError(f"o clone {clone} tem alterações não enviadas; deixe-o limpo antes")
    if _git(clone, "tag", "-l", version).stdout.strip():
        raise ExportError(f"a tag {version} já existe no clone")
    origin = _git(clone, "remote", "get-url", "origin", check=False).stdout.strip()
    if not PUBLIC_REMOTE.search(origin):
        raise ExportError(f"o origin do clone não é o repositório público orto-cvm-ai: {origin or '(nenhum)'}")
    name, email = author or _author()

    for p in clone.iterdir():  # sync: the clone ends up exactly the tree (removed files disappear)
        if p.name != ".git":
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    for f, rel in _files(out):
        dst = clone / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst, follow_symlinks=False)
    _git(clone, "add", "-A", "-f")  # -f: a file the tree ships must go even if a .gitignore pattern matches it
    status = _git(clone, "status", "--short").stdout.rstrip()
    if not status:
        _restore(clone)
        raise ExportError("nada mudou desde a última versão")
    counts: dict[str, int] = {}
    for line in status.splitlines():
        counts[line[:2].strip()] = counts.get(line[:2].strip(), 0) + 1
    print("git status: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if not yes:
        try:
            answer = input(f"Enviar {version} para orto-cvm-ai? [s/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("s", "sim", "y", "yes"):
            _restore(clone)
            raise ExportError("cancelado; o clone voltou ao que era")
    ident = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    r = _git(clone, "rev-parse", "--verify", "-q", "HEAD", check=False)
    head = r.stdout.strip() if r.returncode == 0 else None
    try:
        _git(clone, *ident, "commit", "-qm", f"Release {version}")
        _git(clone, *ident, "tag", "-a", version, "-m", version)
    except ExportError:
        _git(clone, "tag", "-d", version, check=False)
        if head:
            _git(clone, "reset", "-q", "--hard", head)
            _git(clone, "clean", "-fdq")
        else:
            _git(clone, "update-ref", "-d", "HEAD", check=False)
            _restore(clone)
        raise
    push = ["git", "-C", str(clone), "push", "origin", "HEAD:refs/heads/main", f"refs/tags/{version}"]
    r = _git(clone, *push[3:], check=False)
    if r.returncode != 0:
        raise ExportError(f"commit e tag {version} feitos no clone, mas o push falhou ({r.stderr.strip()}). "
                          f"Para enviar à mão: {' '.join(push)}")
    print(f"Enviado: {version} (main + tag)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Monta e envia a cópia pública (orto-cvm-ai).")
    ap.add_argument("--version", required=True, help="vX.Y")
    ap.add_argument("--clone", default=str(ROOT.parent / "orto-cvm-ai"), help="clone local do repositório público")
    ap.add_argument("--dry-run", action="store_true", help="só monta, confere e resume; não faz commit")
    ap.add_argument("--yes", action="store_true", help="não pergunta antes de enviar")
    ap.add_argument("--repo", default=str(ROOT), help=argparse.SUPPRESS)
    ap.add_argument("--out", default=None, help="pasta (vazia) para a árvore montada; padrão: pasta temporária")
    a = ap.parse_args(argv)
    repo = Path(a.repo)
    try:
        if not VERSION.match(a.version):
            raise ExportError(f"versão inválida: {a.version} (use vX.Y)")
        if not a.dry_run and _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise ExportError("este repositório tem alterações sem commit; faça o commit antes de enviar")
        out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="orto-cvm-ai-"))
        files = build_tree(repo, out)
        print(f"Árvore: {out} ({len(files)} arquivos)")
        terms_file = repo / PUBLIC / "terms.txt"
        terms = load_terms(terms_file) if terms_file.is_file() else []
        hits = scan(out, terms)
        print(f"Trava: {len(hits)} termos pessoais encontrados ({len(terms)} termos na lista)")
        if hits:
            for h in hits:
                print("  " + h)
            print("Exportação parada: corrija os pontos acima. O clone não foi tocado.")
            return 1
        _print_summary(summary(out, Path(a.clone)))
        bins = binaries(out)
        print(f"Arquivos binários na cópia ({len(bins)}), confira antes de enviar:")
        for b in bins:
            print("  " + b)
        if a.dry_run:
            print("Simulação: nada foi enviado.")
            return 0
        release(out, Path(a.clone), a.version, yes=a.yes, author=_author(Path(a.clone)))
        return 0
    except ExportError as e:
        print(f"Erro: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
