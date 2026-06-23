#!/usr/bin/env python3
"""
bot.safety.logger — Structured logging configuration for the trading bot.

Configures structlog (26.1.0) with a rotating JSON log file and a readable
stderr ConsoleRenderer. Supports contextvars so the same context flows across
asyncio boundaries. Preserves [WARN]/[ERROR] severity semantics inherited from
the skills/ codebase (SVC-03).

Exports: configure_logging, get_logger
"""
import logging
import logging.handlers
import os
import sys

import structlog

# ============================================================
# Constants
# ============================================================

_DEFAULT_LOG_DIR = "logs"
_DEFAULT_LEVEL = "INFO"
_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
_BACKUP_COUNT = 5                # keep 5 rotated files

# Map level name strings to logging constants
_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# ============================================================
# Internal helpers
# ============================================================

def _add_severity_prefix(logger, method, event_dict):  # noqa: ARG001
    """Structlog processor: add [WARN]/[ERROR] prefix to stderr renderer.

    Preserves the [WARN]/[ERROR] convention from skills/moomooapi/scripts/common.py.
    Only applied to the console (stderr) renderer; the JSON file renderer gets the
    raw level field instead.
    """
    level = event_dict.get("level", "").upper()
    if level in ("WARNING", "WARN"):
        event_dict["_severity_prefix"] = "[WARN]"
    elif level == "ERROR":
        event_dict["_severity_prefix"] = "[ERROR]"
    return event_dict


# ============================================================
# Public API
# ============================================================

_configured = False


def configure_logging(log_dir: str = _DEFAULT_LOG_DIR, level: str = _DEFAULT_LEVEL) -> None:
    """Configure structlog with a rotating JSON file + readable stderr output.

    Sets up two output channels:
    - Rotating file handler under log_dir/ emitting JSON-rendered events.
    - Stderr handler emitting human-readable ConsoleRenderer output.

    Uses contextvars integration so structured context (bound key-values)
    propagates across asyncio boundaries. Call once at bot startup.

    log_dir: str — directory to create bot.log and its rotations (created
        if it does not exist).
    level: str — minimum log level string ("DEBUG", "INFO", "WARNING",
        "ERROR"). Default "INFO".
    """
    global _configured
    if _configured:
        return

    numeric_level = _LEVEL_MAP.get(level.upper(), logging.INFO)

    # ---- ensure log directory exists ----
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "bot.log")

    # ---- stdlib root logger ----
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove default handlers to avoid duplicates
    root_logger.handlers.clear()

    # ---- rotating file handler (JSON output) ----
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)

    # ---- stderr console handler (human-readable) ----
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(numeric_level)

    # Plain formatter for stderr — structlog renders the full line
    plain_fmt = logging.Formatter("%(message)s")
    stderr_handler.setFormatter(plain_fmt)
    file_handler.setFormatter(plain_fmt)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stderr_handler)

    # ---- shared processor chain ----
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
    ]

    # ---- structlog configuration ----
    structlog.configure(
        processors=shared_processors + [
            # Format exceptions as strings before rendering
            structlog.processors.format_exc_info,
            # Route through stdlib logging (which fans out to both handlers)
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    # ---- per-handler renderers via ProcessorFormatter ----
    # File handler: JSON renderer (structured, machine-readable)
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=shared_processors,
        )
    )

    # Stderr handler: ConsoleRenderer (human-readable, coloured if TTY)
    stderr_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
            foreign_pre_chain=shared_processors,
        )
    )

    _configured = True


def get_logger(name: str = None):
    """Return a bound structlog logger.

    name: str — optional logger name (used as logger_name in log events).
        Defaults to the root logger name.
    Returns a structlog BoundLogger instance.
    """
    if name:
        return structlog.get_logger(name)
    return structlog.get_logger()
