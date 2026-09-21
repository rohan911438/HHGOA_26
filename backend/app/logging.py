"""Structured logging for investigations.

Every investigation emits a stream of named events (see InvestigationEvent)
so a case can be replayed from the log alone. A redaction filter strips
anything that looks like a credential before it reaches a handler.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------- events


class InvestigationEvent:
    """Canonical event names. Used as the `event` field on log records."""

    CASE_STARTED = "CASE_STARTED"
    GRAPH_QUERY = "GRAPH_QUERY"
    GRAPH_RESULT = "GRAPH_RESULT"
    GRAPH_UNAVAILABLE = "GRAPH_UNAVAILABLE"
    POLICY_RETRIEVAL = "POLICY_RETRIEVAL"
    SIMILAR_CASE_RETRIEVAL = "SIMILAR_CASE_RETRIEVAL"
    RISK_ASSESSMENT = "RISK_ASSESSMENT"
    UNCERTAINTY_DETECTED = "UNCERTAINTY_DETECTED"
    EVIDENCE_REQUESTED = "EVIDENCE_REQUESTED"
    EVIDENCE_RECEIVED = "EVIDENCE_RECEIVED"
    ACTION_RECOMMENDED = "ACTION_RECOMMENDED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    CASE_UPDATED = "CASE_UPDATED"
    CASE_COMPLETED = "CASE_COMPLETED"
    CASE_FAILED = "CASE_FAILED"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_FAILED = "TOOL_FAILED"
    LLM_CALLED = "LLM_CALLED"
    LLM_FAILED = "LLM_FAILED"


# ---------------------------------------------------------------- redaction

_SECRET_KEY_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|jwt|bearer)\b"
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")

_REDACTED = "***REDACTED***"


def scrub(value: Any) -> Any:
    """Recursively strip credential-shaped values from a payload."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = _REDACTED
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        value = _BEARER_RE.sub(f"Bearer {_REDACTED}", value)
        value = _SK_RE.sub(_REDACTED, value)
        return value
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if hasattr(record, "payload"):
            record.payload = scrub(record.payload)
        return True


# ---------------------------------------------------------------- formatter

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


# ---------------------------------------------------------------- setup

_configured = False


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactionFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
        )
    root.addHandler(handler)

    # Third-party noise
    for noisy in ("httpx", "httpcore", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel("WARNING")

    _configured = True


def get_logger(name: str) -> logging.Logger:
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    event: str,
    message: str = "",
    *,
    case_id: str | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one structured investigation event."""
    extra: dict[str, Any] = {"event": event}
    if case_id:
        extra["case_id"] = case_id
    if fields:
        extra["payload"] = scrub(fields)
    logger.log(level, message or event, extra=extra)
