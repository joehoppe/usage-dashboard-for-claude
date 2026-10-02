"""ClaudeCliRefresher spawn tests — always against a stub, never the real claude."""

import json
import logging
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from claude_usage.infrastructure.claude_cli import (
    ClaudeCliRefresher,
    ClaudeNotFound,
    RefreshOutcome,
    resolve_claude,
)


def write_stub(tmp_path: Path, body: str) -> str:
    """An executable claude stand-in; `body` is the Python the stub runs.

    A wrapper script rather than a bare binary — mirroring how `claude`
    resolves in the wild (npm .cmd shim on Windows, shell shim elsewhere),
    which is exactly the case the design's §5 Windows note flags.
    """
    script = tmp_path / "stub_body.py"
    script.write_text(body, encoding="utf-8")
    if os.name == "nt":
        exe = tmp_path / "claude.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        exe = tmp_path / "claude"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


class RecordingLog(logging.Handler):
    """A real Logger whose entries are kept in memory as (level, message)."""

    def __init__(self, name):
        super().__init__()
        self.entries = []
        self.logger = logging.getLogger(name)
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self)

    def emit(self, record):
        self.entries.append((record.levelname, record.getMessage()))


@pytest.fixture
def log(request):
    recording = RecordingLog(f"tests.claude_cli.{request.node.name}")
    yield recording
    recording.logger.removeHandler(recording)


def no_claude_on_path(monkeypatch):
    monkeypatch.setattr(
        "claude_usage.infrastructure.claude_cli.shutil.which",
        lambda name, path=None: None,
    )


# What shutil.which("claude") finds in a PATH folder on this OS.
ON_PATH_NAME = "claude.exe" if os.name == "nt" else "claude"


def make_runnable(path: Path) -> str:
    """A file that passes resolve_claude's check. Resolver tests never spawn it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def fallback_dir(home: Path) -> Path:
    return home / ".local" / "bin"


@pytest.fixture
def empty_path(tmp_path):
    """An env whose PATH is a real, empty folder — searching it finds nothing."""
    folder = tmp_path / "empty-path"
    folder.mkdir()
    return {"PATH": str(folder)}


def test_exit_zero_is_refreshed(tmp_path):
    exe = write_stub(tmp_path, "raise SystemExit(0)")
    assert ClaudeCliRefresher(executable=exe).refresh() is RefreshOutcome.REFRESHED


def test_nonzero_exit_is_failed(tmp_path):
    exe = write_stub(tmp_path, "raise SystemExit(3)")
    assert ClaudeCliRefresher(executable=exe).refresh() is RefreshOutcome.FAILED


def test_timeout_is_timed_out(tmp_path):
    exe = write_stub(tmp_path, "import time; time.sleep(30)")
    refresher = ClaudeCliRefresher(executable=exe, timeout_seconds=1)
    assert refresher.refresh() is RefreshOutcome.TIMED_OUT


def test_no_executable_resolved_is_not_found(monkeypatch, tmp_path):
    no_claude_on_path(monkeypatch)
    assert ClaudeCliRefresher(home=tmp_path).refresh() is RefreshOutcome.NOT_FOUND


def test_explicit_executable_that_is_missing_is_not_found(tmp_path):
    # A configured path is checked before spawning, and never falls back
    # (lookup spec §4 step 1′) — a fallback stub here must not be used.
    make_runnable(fallback_dir(tmp_path) / "claude")
    missing = str(tmp_path / "nope")
    refresher = ClaudeCliRefresher(executable=missing, home=tmp_path)
    assert refresher.refresh() is RefreshOutcome.NOT_FOUND


def test_argv_is_exactly_dash_p_usage(tmp_path):
    log = tmp_path / "argv.json"
    exe = write_stub(
        tmp_path,
        "import json, sys, pathlib\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\n",
    )
    ClaudeCliRefresher(executable=exe).refresh()
    assert json.loads(log.read_text(encoding="utf-8")) == ["-p", "/usage"]


def test_stdin_sees_eof_instead_of_blocking(tmp_path):
    exe = write_stub(tmp_path, "import sys\nsys.stdin.read()\nraise SystemExit(0)")
    refresher = ClaudeCliRefresher(executable=exe, timeout_seconds=10)
    assert refresher.refresh() is RefreshOutcome.REFRESHED


@pytest.mark.skipif(os.name != "nt", reason="console windows are a Windows concept")
def test_child_gets_no_console_window(tmp_path):
    """A console-subsystem child spawned from a console-less parent is handed
    a brand-new console window — the terminal that flashes on Refresh. The
    npm claude.cmd shim is exactly that (cmd.exe, then node.exe), and the app
    is launched with pythonw, which has no console to inherit.

    The spawn must therefore happen from a console-less parent or the bug is
    invisible: pytest itself runs attached to a console (often a ConPTY, whose
    GetConsoleWindow is 0), which the child inherits, so no window is ever
    created. DETACHED_PROCESS reproduces the real condition faithfully.
    """
    log = tmp_path / "console.json"
    exe = write_stub(
        tmp_path,
        "import ctypes, json, pathlib\n"
        f"pathlib.Path({str(log)!r}).write_text("
        "json.dumps(ctypes.windll.kernel32.GetConsoleWindow()))\n",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys\n"
        "from claude_usage.infrastructure.claude_cli import ClaudeCliRefresher\n"
        "ClaudeCliRefresher(executable=sys.argv[1]).refresh()\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(driver), exe],
        creationflags=subprocess.DETACHED_PROCESS,
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        timeout=120,
        check=True,
    )
    assert json.loads(log.read_text(encoding="utf-8")) == 0


def test_not_found_logs_the_searched_path(monkeypatch, tmp_path, log):
    no_claude_on_path(monkeypatch)
    ClaudeCliRefresher(log=log.logger, env={"PATH": "/usr/bin:/bin"}, home=tmp_path).refresh()
    assert log.entries == [
        (
            "ERROR",
            "refresh not_found: claude_executable unset; searched PATH=/usr/bin:/bin; "
            f"fallback={fallback_dir(tmp_path)}",
        )
    ]


def test_not_found_with_no_path_says_unset(monkeypatch, tmp_path, log):
    no_claude_on_path(monkeypatch)
    ClaudeCliRefresher(log=log.logger, env={}, home=tmp_path).refresh()
    assert log.entries == [
        (
            "ERROR",
            "refresh not_found: claude_executable unset; searched PATH=<unset>; "
            f"fallback={fallback_dir(tmp_path)}",
        )
    ]


def test_which_searches_the_injected_path(tmp_path, log):
    # The logged PATH must be the PATH searched: a stub reachable only
    # through the injected PATH must be found.
    write_stub(tmp_path, "raise SystemExit(0)")
    refresher = ClaudeCliRefresher(
        log=log.logger, env={"PATH": str(tmp_path)}, home=tmp_path / "home"
    )
    assert refresher.refresh() is RefreshOutcome.REFRESHED


def test_timeout_logs_the_limit_and_executable(tmp_path, log):
    exe = write_stub(tmp_path, "import time; time.sleep(30)")
    ClaudeCliRefresher(executable=exe, timeout_seconds=1, log=log.logger).refresh()
    assert log.entries == [("ERROR", f"refresh timed_out after 1s: executable={exe}")]


def test_nonzero_exit_logs_the_code_and_executable(tmp_path, log):
    exe = write_stub(tmp_path, "raise SystemExit(3)")
    ClaudeCliRefresher(executable=exe, log=log.logger).refresh()
    assert log.entries == [("ERROR", f"refresh failed: exit code 3: executable={exe}")]


def test_missing_configured_executable_logs_not_found(tmp_path, log):
    missing = str(tmp_path / "nope")
    ClaudeCliRefresher(executable=missing, log=log.logger, home=tmp_path).refresh()
    assert log.entries == [
        ("ERROR", f"refresh not_found: claude_executable={missing} is missing or not executable")
    ]


def test_success_logs_nothing(tmp_path, log):
    exe = write_stub(tmp_path, "raise SystemExit(0)")
    ClaudeCliRefresher(executable=exe, log=log.logger).refresh()
    assert log.entries == []


def test_child_output_is_never_logged(tmp_path, log):
    # The child's output may contain account details (module docstring).
    exe = write_stub(
        tmp_path,
        "import sys\n"
        "print('SENTINEL-STDOUT')\n"
        "print('SENTINEL-STDERR', file=sys.stderr)\n"
        "raise SystemExit(3)\n",
    )
    ClaudeCliRefresher(executable=exe, log=log.logger).refresh()
    assert len(log.entries) == 1
    assert all("SENTINEL" not in message for _, message in log.entries)


def test_resolve_returns_a_runnable_configured_path(tmp_path, empty_path):
    exe = make_runnable(tmp_path / "custom" / "claude")
    assert resolve_claude(exe, empty_path, tmp_path) == exe


def test_resolve_missing_configured_path_does_not_fall_back(tmp_path, empty_path):
    make_runnable(fallback_dir(tmp_path) / "claude")
    missing = str(tmp_path / "nope" / "claude")
    assert resolve_claude(missing, empty_path, tmp_path) == ClaudeNotFound(
        f"claude_executable={missing} is missing or not executable"
    )


@pytest.mark.skipif(os.name == "nt", reason="on Windows X_OK reduces to existence")
def test_resolve_configured_file_that_is_not_executable_is_not_found(tmp_path, empty_path):
    plain = tmp_path / "claude"
    plain.write_text("not executable\n", encoding="utf-8")
    plain.chmod(0o644)
    assert resolve_claude(str(plain), empty_path, tmp_path) == ClaudeNotFound(
        f"claude_executable={plain} is missing or not executable"
    )


def test_resolve_configured_directory_is_not_found(tmp_path, empty_path):
    folder = fallback_dir(tmp_path)
    make_runnable(folder / "claude")
    assert resolve_claude(str(folder), empty_path, tmp_path) == ClaudeNotFound(
        f"claude_executable={folder} is missing or not executable"
    )


def test_resolve_bare_configured_name_is_not_found(tmp_path):
    # Spec §4 checks the configured value as a path. A bare name is not
    # looked up on PATH, even when PATH has it.
    path_dir = tmp_path / "path-bin"
    make_runnable(path_dir / ON_PATH_NAME)
    assert resolve_claude("claude", {"PATH": str(path_dir)}, tmp_path) == ClaudeNotFound(
        "claude_executable=claude is missing or not executable"
    )


def test_resolve_empty_configured_is_unset(tmp_path, empty_path):
    expected = make_runnable(fallback_dir(tmp_path) / "claude")
    assert resolve_claude("", empty_path, tmp_path) == expected


def test_resolve_path_wins_over_the_fallback(tmp_path):
    path_dir = tmp_path / "path-bin"
    make_runnable(path_dir / ON_PATH_NAME)
    make_runnable(fallback_dir(tmp_path) / "claude")
    found = resolve_claude(None, {"PATH": str(path_dir)}, tmp_path)
    # Compare folders: on Windows which() may return the PATHEXT spelling.
    assert isinstance(found, str)
    assert Path(found).parent == path_dir


def test_resolve_falls_back_to_the_native_launcher(tmp_path, empty_path):
    expected = make_runnable(fallback_dir(tmp_path) / "claude")
    assert resolve_claude(None, empty_path, tmp_path) == expected


def test_resolve_falls_back_to_the_windows_launcher(tmp_path, empty_path):
    expected = make_runnable(fallback_dir(tmp_path) / "claude.exe")
    assert resolve_claude(None, empty_path, tmp_path) == expected


def test_resolve_dangling_launcher_symlink_is_not_found(tmp_path, empty_path):
    link = fallback_dir(tmp_path) / "claude"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(tmp_path / "versions" / "gone")
    except OSError:
        pytest.skip("this OS cannot create symlinks here")
    assert isinstance(resolve_claude(None, empty_path, tmp_path), ClaudeNotFound)


def test_resolve_nothing_found_names_path_and_fallback(tmp_path, empty_path):
    assert resolve_claude(None, empty_path, tmp_path) == ClaudeNotFound(
        f"claude_executable unset; searched PATH={empty_path['PATH']}; "
        f"fallback={fallback_dir(tmp_path)}"
    )


def test_resolve_nothing_without_path_says_unset(monkeypatch, tmp_path):
    # With path=None, which() would search the process PATH, which may hold
    # the real claude.
    no_claude_on_path(monkeypatch)
    assert resolve_claude(None, {}, tmp_path) == ClaudeNotFound(
        f"claude_executable unset; searched PATH=<unset>; fallback={fallback_dir(tmp_path)}"
    )


def write_unrunnable(tmp_path: Path) -> str:
    """Passes the lookup's check, but the OS refuses to run it: no shebang on
    POSIX (ENOEXEC), not a PE image on Windows."""
    exe = tmp_path / ("claude.exe" if os.name == "nt" else "claude")
    exe.write_bytes(b"this is not a program\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def test_found_executable_that_cannot_run_is_failed(tmp_path, log):
    # Found but unrunnable is still FAILED, logged by exception type only.
    exe = write_unrunnable(tmp_path)
    refresher = ClaudeCliRefresher(executable=exe, log=log.logger, home=tmp_path)
    assert refresher.refresh() is RefreshOutcome.FAILED
    [(level, message)] = log.entries
    assert level == "ERROR"
    assert re.fullmatch(rf"refresh failed: \w+: executable={re.escape(exe)}", message)


@pytest.mark.skipif(
    os.name == "nt", reason="write_stub makes claude.cmd, which is not a fallback name"
)
def test_fallback_launcher_is_spawned_when_path_has_none(tmp_path, empty_path):
    home = tmp_path / "home"
    launcher_dir = fallback_dir(home)
    launcher_dir.mkdir(parents=True)
    write_stub(launcher_dir, "raise SystemExit(0)")
    refresher = ClaudeCliRefresher(env=empty_path, home=home)
    assert refresher.refresh() is RefreshOutcome.REFRESHED
