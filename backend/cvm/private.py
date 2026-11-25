"""The `private/` overlay: owner-only rules, read when present and harmless when absent.

`private/` sits at the repo root and is left out of the public copy. Convention:
  private/agents/<agent>.md           appended to that agent's instructions (prompt text)
  private/skills/<skill>.md           appended to that skill (e.g. script-style-kids)
  private/channel_rules/<handle>.md   appended to the channel brief; <handle> is the channel's youtube_handle
                                      without "@" (lower case), else its name slug, else its numeric id
"""

import json as _json
import re
from pathlib import Path

from cvm.config import REPO_ROOT

PRIVATE_DIR = REPO_ROOT / "private"


def text(name: str) -> str:
    """Contents of private/<name> ("" when the folder or file is absent)."""
    try:
        return (PRIVATE_DIR / name).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def json(name: str, default=None):
    raw = text(name)
    if not raw:
        return default
    try:
        return _json.loads(raw)
    except ValueError:
        return default


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (value or "").lower().lstrip("@")).strip("-")


def agent_rules(agent: str) -> str:
    return text(f"agents/{_slug(agent)}.md")


def skill_rules(skill: str) -> str:
    return text(f"skills/{_slug(skill.replace('_', '-'))}.md")


def channel_rules(channel) -> str:
    cid = getattr(channel, "id", None)
    for key in (getattr(channel, "youtube_handle", None), getattr(channel, "name", None),
                str(cid) if cid is not None else None):
        if key and _slug(key) and (t := text(f"channel_rules/{_slug(key)}.md")):
            return t
    return ""
