"""CVM install helper: fetches a tool's weights into its own folder, the same on Windows and macOS (no curl, unzip,
ln or bash needed). Run with the tool's venv python from the tool folder (cvm/setup/tools.py):

    python cvm_fetch.py hf <repo> <dest_dir> [pattern ...]     # Hugging Face snapshot into dest_dir
    python cvm_fetch.py url <url> <dest_file>                  # plain download
    python cvm_fetch.py zip <url> <dest_dir> <member ...>      # only these members of a zip, flattened
    python cvm_fetch.py gdrive <file_id> <dest_file>           # Google Drive file (gdown)
    python cvm_fetch.py copy <src> <dest>                      # a file copied where a library looks for it

Every command skips what is already there, so a re-run only fetches what is missing.
"""

import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f)
    part.replace(dest)


def main(argv: list[str]) -> int:
    cmd, args = argv[0], argv[1:]
    if cmd == "hf":
        from huggingface_hub import snapshot_download

        repo, dest, patterns = args[0], args[1], args[2:] or None
        snapshot_download(repo, local_dir=dest, allow_patterns=patterns)
    elif cmd == "url":
        dest = Path(args[1])
        if not dest.exists():
            _download(args[0], dest)
    elif cmd == "zip":
        url, dest, members = args[0], Path(args[1]), args[2:]
        if all((dest / Path(m).name).exists() for m in members):
            return 0
        with tempfile.TemporaryDirectory() as td:
            z = Path(td) / "a.zip"
            _download(url, z)
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
                for m in members:
                    name = next((n for n in names if n == m or n.endswith("/" + m)), None)
                    if name is None:
                        raise SystemExit(f"{m} não está em {url}")
                    dest.mkdir(parents=True, exist_ok=True)
                    with zf.open(name) as src, open(dest / Path(m).name, "wb") as out:
                        shutil.copyfileobj(src, out)
    elif cmd == "gdrive":
        dest = Path(args[1])
        if not dest.exists():
            import gdown

            dest.parent.mkdir(parents=True, exist_ok=True)
            gdown.download(id=args[0], output=str(dest), quiet=True)
    elif cmd == "copy":
        src, dest = Path(args[0]), Path(args[1])
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
