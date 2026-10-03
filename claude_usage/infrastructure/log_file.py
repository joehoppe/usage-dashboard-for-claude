"""Rotating log file for failed refreshes. wx.StandardPaths has no log
folder, so the folder is chosen from the environment rather than from
sys.platform. Opening the log never raises: if the file cannot be set up, the
logger gets a NullHandler and the app runs without a log.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "claude_usage.refresh"
_APP_FOLDER = "claude-usage"
_FILE_NAME = "claude-usage.log"
_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def default_log_path(env: Mapping[str, str], home: Path) -> Path:
    """%LOCALAPPDATA%\\claude-usage\\Logs on Windows; otherwise (macOS)
    ~/Library/Logs/claude-usage, where Console.app also looks.
    """
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / _APP_FOLDER / "Logs" / _FILE_NAME
    return home / "Library" / "Logs" / _APP_FOLDER / _FILE_NAME


def open_refresh_log(path: Path) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    # Replace, never add: a second call must not write every entry twice.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # delay=True: a machine that never fails never gets a log file.
        file_handler = RotatingFileHandler(
            path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
    except OSError:
        logger.addHandler(logging.NullHandler())
        return logger
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(file_handler)
    return logger
