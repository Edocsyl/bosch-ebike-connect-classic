"""Logging setup. One logger family ("ebike.*"); never log passwords, tokens, cookies or codes."""

from __future__ import annotations

import logging

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_ALIASES = {"WARN": "WARNING", "CRITICAL": "ERROR", "FATAL": "ERROR", "TRACE": "DEBUG"}

log = logging.getLogger("ebike")


def parse_level(value: str) -> str:
    v = (value or "INFO").strip().upper()
    v = _ALIASES.get(v, v)
    if v not in LEVELS:
        raise SystemExit(f"LOG_LEVEL must be one of {', '.join(LEVELS)} (got '{value}')")
    return v


def setup_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", force=True)
    # httpx logs every request URL at INFO; our own debug lines cover that
    logging.getLogger("httpx").setLevel(logging.WARNING if level != "DEBUG" else logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def mask_email(email: str) -> str:
    """a***@b***.ch: enough to tell accounts apart in logs, not enough to identify the person."""
    local, _, domain = (email or "").partition("@")
    host, _, tld = domain.rpartition(".")
    return f"{local[:1]}***@{host[:1]}***.{tld}" if local and host and tld else "***"


def clean(value: str, limit: int = 100) -> str:
    """Make untrusted text safe for a log line or a display name: no control characters
    (log injection), bounded length."""
    return "".join(c if c.isprintable() else " " for c in str(value))[:limit].strip()
