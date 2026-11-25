"""Move CVM to another machine without the generated videos.

    uv run cvm-export [out.zip]   # code + database + avatars/voices/channels (+ secrets, music) + Claude memory
    uv run cvm-import             # on the new machine, after unzipping: point the stored paths at the new folder

Left out: data/projects (generated videos), bakeoff/logs/queue, browser profiles (sign in again), model weights
(models/ and the Hugging Face cache, ~80 GB: download again or copy by hand), node_modules/.venv/.next, avatar
backups. MIGRAR.md inside the zip tells the next Claude Code session what to do.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from cvm.config import REPO_ROOT, settings

DATA_DIRS = ["avatars", "channels", "secrets", "music"]
SKIP_PARTS = ("backup",)  # data/avatars/<id>/*backup* folders: old versions, not referenced
MANIFEST = "data/export.json"
MEMORY_DIR = Path.home() / ".claude" / "projects" / ("-" + str(REPO_ROOT).strip("/").replace("/", "-")) / "memory"

GUIDE = """# Migrar o Orto CVM para esta máquina

Este zip veio de `{old_root}` (exportado em {date}, commit {commit}).
Tem o código, o banco (canais, projetos, roteiros, publicações, configurações), os avatares com vozes clonadas e
as vozes dos personagens. NÃO tem os vídeos gerados (data/projects), os pesos dos modelos nem os logins do Chrome.

## Para o Claude Code nesta máquina
Leia CLAUDE.md e depois faça, pedindo confirmação ao usuário antes de downloads grandes:

1. Instalar as ferramentas: Python 3.12 + uv, Node 20+, ffmpeg completo (macOS: `brew install ffmpeg-full`; em
   outro sistema, ajustar `CVM_FFMPEG_BIN` no `.env`), Google Chrome (publicação assistida).
2. `cd backend && uv sync`, depois `uv run cvm-import` (aponta os caminhos do banco para esta pasta).
3. `cd frontend && npm install`.
4. Modelos (~80 GB): `scripts/download-models.sh` e o cache do Hugging Face (FLUX schnell, LTX-2.3 subset,
   Gemma 3 12B 4-bit, VoxCPM2, Whisper). Os modelos do MLX só rodam em Apple Silicon; numa máquina com
   GPU NVIDIA é preciso o backend CUDA (planejado, ainda não feito: ver a memória "status").
5. Memória do Claude: copie `claude-memory/*` para a pasta de memória deste projeto
   (`~/.claude/projects/<caminho do projeto com / trocado por ->/memory/`).
6. Subir: `uv run cvm-api`, `uv run cvm-worker`, `npm run dev` (UI em :3010) e testar um vídeo curto.
7. Publicação assistida: em cada canal, "Fazer login no canal (1ª vez)".

Projetos que estavam no meio da produção perderam os arquivos de trabalho: refaça-os ou exclua-os na fila.
"""


UPDATE_GUIDE = """# Atualizar o Orto CVM já instalado nesta máquina

Pacote de atualização gerado em `{old_root}` em {date} (commit {commit} + mudanças ainda não commitadas).
Traz o código novo, os dados de canais/avatares (vinhetas, artes dos canais, fotos das capas) e um banco novo em
`data/cvm.sqlite.update` — o banco desta máquina NÃO é substituído ao extrair.

## Para o Claude Code nesta máquina
Leia CLAUDE.md (seção "Video structure") e depois, pedindo confirmação ao usuário nos passos que apagam algo:

1. Pare a API, o worker e a UI se estiverem rodando.
2. Backup: copie `data/cvm.sqlite` para `data/cvm.sqlite.bak-<data>` (se existir).
3. Este zip já foi extraído por cima da pasta instalada (código e `data/avatars`, `data/channels` atualizados).
4. Banco — pergunte ao usuário:
   a) "Usar o banco novo do Mac" (nada importante foi criado aqui): troque `data/cvm.sqlite` por
      `data/cvm.sqlite.update` e rode `cd backend && uv run cvm-import` (aponta os caminhos para esta pasta).
   b) "Manter o banco desta máquina": copie do `cvm.sqlite.update` para o banco atual, por id de canal, as colunas
      `brief`, `prefs` e `characters` da tabela `channel`, e o campo `packaging`/`intro_end` dos scripts dos
      projetos que existem nos dois bancos; depois rode `uv run cvm-import` (os caminhos de voz dos personagens
      vêm com a pasta do Mac).
5. `cd backend && uv sync`, `cd frontend && npm install` (dependências novas, se houver).
6. Memória do Claude: copie `claude-memory/*` para a pasta de memória deste projeto (sobrescrevendo).
7. Suba de novo e confira /canais e /publicar.

Observação: os modelos de IA do Mac (MLX) não rodam fora do Apple Silicon; narração, imagens e animação nesta
máquina dependem do backend CUDA (planejado).
"""


def _files_from_git() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [REPO_ROOT / f for f in out.split("\0") if f and not f.endswith(".zip") and (REPO_ROOT / f).is_file()]


def _data_files() -> list[Path]:
    files = []
    for d in DATA_DIRS:
        root = settings.data_dir / d
        if root.exists():
            files += [p for p in root.rglob("*") if p.is_file() and p.name != ".DS_Store"
                      and not any(s in part for part in p.relative_to(root).parts for s in SKIP_PARTS)]
    return files


def export(out: Path, update: bool = False) -> Path:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    manifest = {"old_root": str(REPO_ROOT), "exported_at": date, "commit": commit}
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        db = Path(tmp) / "cvm.sqlite"  # consistent copy even while the API/worker are writing (WAL)
        src = sqlite3.connect(settings.db_path)
        dst = sqlite3.connect(db)
        src.backup(dst)
        dst.close()
        src.close()
        top = "content-video-maker"
        for f in _files_from_git():
            z.write(f, f"{top}/{f.relative_to(REPO_ROOT)}")
        for f in _data_files():
            # videos/wavs are already compressed: store them as they are (much faster)
            kind = zipfile.ZIP_STORED if f.suffix.lower() in {".mp4", ".mov", ".wav", ".jpg", ".png", ".mp3"} else zipfile.ZIP_DEFLATED
            z.write(f, f"{top}/{f.relative_to(REPO_ROOT)}", compress_type=kind)
        z.write(db, f"{top}/data/cvm.sqlite" + (".update" if update else ""))
        z.writestr(f"{top}/{MANIFEST}", json.dumps(manifest, indent=2))
        guide, name = (UPDATE_GUIDE, "ATUALIZAR.md") if update else (GUIDE, "MIGRAR.md")
        z.writestr(f"{top}/{name}", guide.format(old_root=REPO_ROOT, date=date, commit=commit or "?"))
        if MEMORY_DIR.exists():
            for f in MEMORY_DIR.glob("*.md"):
                z.write(f, f"{top}/claude-memory/{f.name}")
    return out


def rewrite_paths(db_path: Path, old: str, new: str) -> int:
    """Replace the old project folder by the new one in every text column (plain paths and JSON alike)."""
    con = sqlite3.connect(db_path)
    changed = 0
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    for t in tables:
        for _, col, typ, *_ in con.execute(f'pragma table_info("{t}")').fetchall():
            if (typ or "").upper() not in ("TEXT", "VARCHAR", "JSON", ""):
                continue
            cur = con.execute(f'update "{t}" set "{col}" = replace("{col}", ?, ?) where "{col}" like ?', (old, new, f"%{old}%"))
            changed += cur.rowcount
    con.commit()
    con.close()
    return changed


def main_export() -> None:
    """uv run cvm-export [--update] [out.zip] — --update: for a machine that already has CVM (keeps its database)."""
    args = [a for a in sys.argv[1:] if a != "--update"]
    update = "--update" in sys.argv[1:]
    kind = "update" if update else "export"
    out = Path(args[0]) if args else Path.home() / "Desktop" / f"cvm-{kind}-{datetime.now():%Y-%m-%d}.zip"
    path = export(out.expanduser().resolve(), update)
    print(f"{path} ({path.stat().st_size / 1e6:.0f} MB)")


def main_import() -> None:
    manifest = settings.data_dir / "export.json"
    if not manifest.exists():
        sys.exit("data/export.json não encontrado: rode dentro da pasta extraída do zip do cvm-export")
    old, new = json.loads(manifest.read_text())["old_root"], REPO_ROOT.as_posix()
    if old == new:
        print("mesma pasta da exportação: nada a ajustar")
        return
    n = rewrite_paths(settings.db_path, old, new)
    print(f"{n} registros apontados de {old} para {new}")
