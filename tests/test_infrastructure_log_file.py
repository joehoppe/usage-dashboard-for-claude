"""Refresh log file tests — temp paths only, never the real log folder."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from claude_usage.infrastructure.log_file import (
    LOGGER_NAME,
    default_log_path,
    open_refresh_log,
)


@pytest.fixture(autouse=True)
def release_logger():
    # The logger is process-wide. Close its file handle so Windows can delete
    # tmp_path, and so no test inherits another test's handler.
    yield
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_windows_log_lives_under_localappdata():
    # Path arithmetic, not a literal "C:\x\..." string: off Windows, "C:\x"
    # is just a folder name, and this test runs on every matrix leg.
    path = default_log_path({"LOCALAPPDATA": "C:\\x"}, Path("unused-home"))
    assert path == Path("C:\\x") / "claude-usage" / "Logs" / "claude-usage.log"


def test_macos_log_lives_under_library_logs(tmp_path):
    expected = tmp_path / "Library" / "Logs" / "claude-usage" / "claude-usage.log"
    assert default_log_path({}, tmp_path) == expected


def test_writes_an_error_entry(tmp_path):
    path = tmp_path / "Logs" / "claude-usage.log"  # parent does not exist yet
    open_refresh_log(path).error("refresh not_found: PATH=%s", "/usr/bin")
    assert " ERROR refresh not_found: PATH=/usr/bin\n" in path.read_text(encoding="utf-8")


def test_rotates_at_one_megabyte_keeping_three_backups(tmp_path):
    (handler,) = open_refresh_log(tmp_path / "claude-usage.log").handlers
    assert isinstance(handler, RotatingFileHandler)
    assert handler.maxBytes == 1_000_000
    assert handler.backupCount == 3


def test_does_not_propagate_to_the_root_logger(tmp_path):
    assert open_refresh_log(tmp_path / "claude-usage.log").propagate is False


def test_no_file_until_the_first_entry(tmp_path):
    path = tmp_path / "claude-usage.log"
    open_refresh_log(path)
    assert not path.exists()


def test_opening_twice_does_not_double_entries(tmp_path):
    path = tmp_path / "claude-usage.log"
    open_refresh_log(path)
    open_refresh_log(path).error("only once")
    assert path.read_text(encoding="utf-8").count("only once") == 1


def test_non_ascii_path_round_trips(tmp_path):
    path = tmp_path / "claude-usage.log"
    open_refresh_log(path).error("searched PATH=%s", "/Users/José/bin")
    assert "/Users/José/bin" in path.read_text(encoding="utf-8")


def test_unusable_folder_falls_back_to_a_null_handler(tmp_path):
    # A file where the folder should be: a portable stand-in for a folder
    # that cannot be created.
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    logger = open_refresh_log(blocker / "claude-usage.log")
    assert [type(handler) for handler in logger.handlers] == [logging.NullHandler]
    logger.error("the app keeps running")  # must not raise


def test_a_write_failure_never_reaches_the_caller(tmp_path, capsys):
    target = tmp_path / "is-a-folder"
    target.mkdir()  # opening a folder as the log file fails only at emit time
    open_refresh_log(target).error("refresh failed")  # must not raise
    assert "--- Logging error ---" in capsys.readouterr().err
