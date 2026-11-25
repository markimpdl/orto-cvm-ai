"""API keys typed in Configurações > Integrações: kept in the macOS Keychain (the `security` CLI, fed through stdin so
the key never shows in the process list; the `keyring` package elsewhere when installed). Never in the DB, a log,
a response or git. Read order: Keychain, then .env (settings) as the fallback.
"""

import re
import subprocess
import sys
import time

SERVICE = "cvm"
ELEVENLABS = "elevenlabs"
CACHE_S = 60.0
_VALID = re.compile(r"^[A-Za-z0-9_\-.]{16,200}$")
_cache: dict[str, tuple[float, str | None]] = {}


class SecretError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring  # optional; not a dependency

        return keyring
    except Exception:  # noqa: BLE001
        return None


def _store_get(name: str) -> str | None:
    if sys.platform == "darwin":
        r = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                           capture_output=True, text=True)
        return (r.stdout.strip() or None) if r.returncode == 0 else None
    kr = _keyring()
    return kr.get_password(SERVICE, name) if kr else None


def _store_set(name: str, value: str) -> None:
    if sys.platform == "darwin":
        r = subprocess.run(["security", "-i"], input=f'add-generic-password -U -s {SERVICE} -a "{name}" -w "{value}"\n',
                           text=True, capture_output=True)
        if r.returncode != 0:
            raise SecretError("não consegui gravar no Keychain")
        return
    kr = _keyring()
    if not kr:
        raise SecretError("sem cofre de senhas nesta máquina: use o .env")
    kr.set_password(SERVICE, name, value)


def _store_delete(name: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name], capture_output=True)
        return
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(SERVICE, name)
        except Exception:  # noqa: BLE001
            pass


def get(name: str) -> str | None:
    hit = _cache.get(name)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]
    try:
        value = _store_get(name)
    except Exception:  # noqa: BLE001
        value = None
    _cache[name] = (time.monotonic(), value)
    return value


def put(name: str, value: str) -> None:
    value = (value or "").strip()
    if not _VALID.match(value):
        raise ValueError("chave em formato inválido")
    _store_set(name, value)
    _cache.pop(name, None)


def delete(name: str) -> None:
    _store_delete(name)
    _cache.pop(name, None)


def elevenlabs_key() -> str | None:
    """Keychain first, then CVM_ELEVENLABS_API_KEY / ELEVENLABS_API_KEY from .env."""
    from cvm.config import settings

    return get(ELEVENLABS) or settings.elevenlabs_api_key or None


def elevenlabs_source() -> str | None:
    from cvm.config import settings

    if get(ELEVENLABS):
        return "keychain"
    return "env" if settings.elevenlabs_api_key else None
