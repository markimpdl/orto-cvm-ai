"""Popen options for a background run that must outlive the API that starts it (install, update, bumper preview).

POSIX: a new session (start_new_session). Windows has no sessions: DETACHED_PROCESS (no console, so closing the API's
window does not end it) + CREATE_NEW_PROCESS_GROUP (a Ctrl+C in that window does not reach it). The numbers are the
Win32 constants, so this module imports on the Mac too (subprocess only defines them on Windows)."""

import subprocess
import sys

DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def detached() -> dict:
    if sys.platform == "win32":
        return {"creationflags": DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def no_window() -> dict:
    """A console program run from a detached process: on Windows, without flashing a new console window."""
    return {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
