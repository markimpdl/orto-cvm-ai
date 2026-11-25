"""Install wizard, AI step: which engine writes (Claude, Codex or both) and whether each CLI is ready.

Never spends a prompt: Claude is checked with `claude auth status` (JSON), Codex with its own login check.
The project only uses the Claude subscription, so an API-key login does not count.
"""
import json
import os
import shutil
import subprocess
import time

from cvm import engines
from cvm.scripting import codex_runner

CACHE_S = 60.0
CHOICES = {"claude": ("claude", False), "both": ("claude", True), "codex": ("codex", False)}
_cache: dict = {"at": 0.0, "value": None}


def invalidate() -> None:
    _cache.update(at=0.0, value=None)


def _claude() -> dict:
    exe = shutil.which("claude")
    if not exe:
        return {"installed": False, "logged_in": False}
    out = {"installed": True, "logged_in": False}
    try:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=10,
                           env=env, stdin=subprocess.DEVNULL)
        data = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return out
    if not isinstance(data, dict) or not data.get("loggedIn"):
        return out
    method = str(data.get("authMethod") or "").lower()
    if method and method not in ("claude.ai", "subscription", "claudeai", "oauth"):
        out["reason"] = "login por chave de API; o projeto usa só a assinatura do Claude"
        return out
    out["logged_in"] = True
    return out


def _codex() -> dict:
    try:
        installed = bool(shutil.which(codex_runner.settings.codex_bin))
        return {"installed": installed, "logged_in": bool(installed and codex_runner.installed())}
    except Exception:  # noqa: BLE001
        return {"installed": False, "logged_in": False}


def status(fresh: bool = False) -> dict:
    if not fresh and _cache["value"] is not None and time.monotonic() - _cache["at"] < CACHE_S:
        return _cache["value"]
    value = {"claude": _claude(), "codex": _codex()}
    _cache.update(at=time.monotonic(), value=value)
    return value


def apply(choice: str) -> dict:
    if choice not in CHOICES:
        raise ValueError(f"choice: {choice!r}")
    writer, fallback = CHOICES[choice]
    return engines.put({"writer": writer, "codex_fallback": fallback})
