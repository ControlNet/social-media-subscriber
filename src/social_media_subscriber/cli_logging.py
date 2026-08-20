"""Secret-safe human and structured CLI failure logging."""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from dataclasses import asdict, dataclass
from typing import Final

from social_media_subscriber.publishing.git import (
    InvalidPublicationError,
    StalePublicationError,
)
from social_media_subscriber.publishing.process import (
    GitCommandError,
    GitInterruptedError,
)
from social_media_subscriber.storage.repository import SnapshotIntegrityError

_URL: Final = re.compile(r"https?://[^\s\"']+")
_REDACTED: Final = "[REDACTED]"


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    """Stable error fields shared by developer and CI renderers."""

    event: str
    error_type: str
    category: str
    message: str
    stack: str


def _category(error: Exception) -> str:
    match error:
        case InvalidPublicationError(category=category):
            return category.value
        case StalePublicationError():
            return "stale_lease"
        case GitCommandError():
            return "git_command"
        case GitInterruptedError():
            return "git_interrupted"
        case SnapshotIntegrityError():
            return "integrity"
        case _:
            return "unhandled"


def _redact(value: str) -> str:
    sanitized = _URL.sub(_REDACTED, value)
    for name in ("ACCOUNTS", "BRIGHT_DATA_API_KEYS"):
        raw = os.environ.get(name, "")
        for secret in (line.strip() for line in raw.splitlines()):
            if secret:
                sanitized = sanitized.replace(secret, _REDACTED)
    return sanitized


def log_exception(error: Exception) -> None:
    """Render one redacted terminal failure with preserved exception context."""
    record = ErrorRecord(
        event="cli.failure",
        error_type=type(error).__name__,
        category=_category(error),
        message=_redact(str(error)),
        stack=_redact("".join(traceback.format_exception(error))),
    )
    if os.environ.get("CI", "").lower() == "true":
        rendered = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
    else:
        rendered = (
            f"ERROR {record.event} type={record.error_type} "
            f"category={record.category} message={record.message}\n{record.stack}"
        )
    _ = sys.stderr.write(f"{rendered}\n")
