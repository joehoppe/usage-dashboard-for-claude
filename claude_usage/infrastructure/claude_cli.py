"""Adapter spawning `claude -p "/usage"`, which makes Claude Code refresh its
own quota cache. This project never writes ~/.claude.json — only the child
does. Captured child output is discarded, never logged: it may contain
account details.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

# Windows hands a console-subsystem child its own new console window when
# the parent has none — the app runs under pythonw, and `claude` resolves
# to the npm claude.cmd shim, i.e. cmd.exe and then node.exe. Redirecting
# the child's handles does not prevent that allocation; only a creation
# flag does. The constant exists on Windows only; 0 is a no-op elsewhere.
NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RefreshOutcome(Enum):
    # The trailing comments are a column: each outcome against what produced it.
    # fmt: off
    REFRESHED = "refreshed"    # process exited 0
    NOT_FOUND = "not_found"    # no claude executable resolved
    TIMED_OUT = "timed_out"    # exceeded refresh_timeout_seconds
    FAILED = "failed"          # non-zero exit, or the spawn itself failed
    # fmt: on


class QuotaRefresher(Protocol):
    def refresh(self) -> RefreshOutcome: ...


@dataclass(frozen=True)
class ClaudeNotFound:
    detail: str  # the log text after "refresh not_found: "


def _is_runnable(path: str) -> bool:
    # isfile follows symlinks, so a dangling launcher counts as missing.
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve_claude(
    configured: str | None, env: Mapping[str, str], home: Path
) -> str | ClaudeNotFound:
    """The `claude` to spawn, or why there is none. Never raises, never spawns.

    A configured path is used only if it passes the check; it never falls
    back. Otherwise: `PATH`, then the native installer's launcher folder.
    Both `claude` and `claude.exe` are tried there, so no platform branch is
    needed and either OS's CI exercises both.
    """
    if configured:
        if _is_runnable(configured):
            return configured
        return ClaudeNotFound(f"claude_executable={configured} is missing or not executable")
    search_path = env.get("PATH")
    found = shutil.which("claude", path=search_path)
    if found is not None:
        return found
    fallback = home / ".local" / "bin"
    for name in ("claude", "claude.exe"):
        candidate = str(fallback / name)
        if _is_runnable(candidate):
            return candidate
    searched = "<unset>" if search_path is None else search_path
    return ClaudeNotFound(f"claude_executable unset; searched PATH={searched}; fallback={fallback}")


class ClaudeCliRefresher:
    def __init__(
        self,
        executable: str | None = None,
        timeout_seconds: int = 60,
        log: logging.Logger | None = None,
        env: Mapping[str, str] = os.environ,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._log = log
        self._env = env

    def refresh(self) -> RefreshOutcome:
        """Never raises: this runs on the refresh worker thread, where an
        escaping exception would die silently and wedge the button on
        "Refreshing…" — every failure mode is a return value.

        Each failure writes one ERROR entry to the injected log, built only
        from what the app already holds: never the child's output, and never
        an exception's message — only its type name.
        """
        search_path = self._env.get("PATH")
        exe = self._executable or shutil.which("claude", path=search_path)
        if exe is None:
            self._error(
                "refresh not_found: claude_executable unset; searched PATH=%s",
                "<unset>" if search_path is None else search_path,
            )
            return RefreshOutcome.NOT_FOUND
        try:
            completed = subprocess.run(
                [exe, "-p", "/usage"],
                shell=False,
                stdin=subprocess.DEVNULL,  # a child that prompts must hit EOF
                capture_output=True,
                creationflags=NO_CONSOLE_WINDOW,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._error("refresh timed_out after %ss: executable=%s", self._timeout_seconds, exe)
            return RefreshOutcome.TIMED_OUT
        except OSError as exc:
            self._error("refresh failed: %s: executable=%s", type(exc).__name__, exe)
            return RefreshOutcome.FAILED
        if completed.returncode == 0:
            return RefreshOutcome.REFRESHED
        self._error("refresh failed: exit code %s: executable=%s", completed.returncode, exe)
        return RefreshOutcome.FAILED

    def _error(self, message: str, *args: object) -> None:
        if self._log is not None:
            self._log.error(message, *args)
