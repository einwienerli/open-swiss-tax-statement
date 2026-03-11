"""Logging configuration with session-based rotation."""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

from src import __version__
from src.ui.paths import get_app_support_dir

SESSION_MARKER = "=" * 80


def _get_log_path() -> str:
    """Return the path to the application log file."""
    return os.path.join(get_app_support_dir(), "app.log")


def _trim_sessions(log_path: str) -> None:
    """Trim the log file to retain only the last 2 sessions.

    After the caller writes a new session header, there will be 3 total.
    No-op if the file doesn't exist or has fewer than 3 sessions.
    """
    if not os.path.exists(log_path):
        return

    with open(log_path, encoding="utf-8") as f:
        content = f.read()
    parts = content.split(SESSION_MARKER)

    # Each session uses 2 markers (top + bottom of header).
    # Split gives 2N+1 parts for N sessions. Parts pair up as:
    #   part[0] = preamble before first session
    #   part[2i-1], part[2i] = session i's header line, log content (i=1..N)
    # To keep last 2 sessions: keep last 4 parts (2 pairs).
    num_markers = len(parts) - 1
    num_sessions = num_markers // 2

    if num_sessions <= 2:
        return

    # Keep last 2 sessions = last 2*2 = 4 parts
    keep_parts = 2 * 2  # 4
    trimmed_parts = parts[-keep_parts:]
    trimmed = SESSION_MARKER.join(trimmed_parts)

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(trimmed)


def setup_logging(debug: bool = False) -> None:
    """Configure application logging with file output and session headers.

    Args:
        debug: If True, set DEBUG level and add stderr handler.
    """
    log_path = _get_log_path()
    log_dir = os.path.dirname(log_path)
    os.makedirs(log_dir, exist_ok=True)

    _trim_sessions(log_path)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Remove any existing handlers
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler (append mode, UTF-8)
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Debug: also log to stderr
    if debug:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    # Write session header directly to file
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n{SESSION_MARKER}\nSESSION START — {now} — v{__version__}\n{SESSION_MARKER}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(header)
