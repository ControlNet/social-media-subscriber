"""Secret-safe human and structured CLI failure logging."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum

from social_media_subscriber.publishing.git import (
    InvalidPublicationError,
    StalePublicationError,
)
from social_media_subscriber.publishing.process import (
    GitCommandError,
    GitInterruptedError,
)
from social_media_subscriber.storage.repository import SnapshotIntegrityError


class CliFailureCategory(StrEnum):
    """Closed machine-readable terminal failure categories."""

    PUBLICATION_INVALID = "publication_invalid"
    STALE_LEASE = "stale_lease"
    GIT_COMMAND = "git_command"
    GIT_INTERRUPTED = "git_interrupted"
    INTEGRITY = "integrity"
    UNHANDLED = "unhandled"


@dataclass(frozen=True, slots=True)
class ErrorRecord:
    """Safe stable fields shared by developer and CI renderers."""

    event: str
    category: CliFailureCategory
    message: str


def _category(error: Exception) -> CliFailureCategory:
    match error:
        case InvalidPublicationError():
            return CliFailureCategory.PUBLICATION_INVALID
        case StalePublicationError():
            return CliFailureCategory.STALE_LEASE
        case GitCommandError():
            return CliFailureCategory.GIT_COMMAND
        case GitInterruptedError():
            return CliFailureCategory.GIT_INTERRUPTED
        case SnapshotIntegrityError():
            return CliFailureCategory.INTEGRITY
        case _:
            return CliFailureCategory.UNHANDLED


def _message(category: CliFailureCategory) -> str:
    match category:
        case CliFailureCategory.PUBLICATION_INVALID:
            return "Publication input or snapshot is invalid"
        case CliFailureCategory.STALE_LEASE:
            return "Publication lease is stale"
        case CliFailureCategory.GIT_COMMAND:
            return "Publication command failed"
        case CliFailureCategory.GIT_INTERRUPTED:
            return "Publication command interrupted"
        case CliFailureCategory.INTEGRITY:
            return "Snapshot integrity validation failed"
        case CliFailureCategory.UNHANDLED:
            return "Unexpected internal failure"


def log_exception(error: Exception) -> None:
    """Render one terminal failure without consuming exception-owned data."""
    category = _category(error)
    record = ErrorRecord(
        event="cli.failure",
        category=category,
        message=_message(category),
    )
    if os.environ.get("CI", "").lower() == "true":
        rendered = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
    else:
        rendered = (
            f"ERROR {record.event} category={record.category.value} "
            f"message={record.message}"
        )
    _ = sys.stderr.write(f"{rendered}\n")
