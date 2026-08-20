"""Secret-safe aggregate collection outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from social_media_subscriber.domain.ids import AccountId


@unique
class CollectionExitCode(IntEnum):
    """Stable machine exit categories for collection orchestration."""

    SUCCESS = 0
    INPUT = 2
    PROVIDER = 3
    PARTIAL = 4
    INTEGRITY = 5


@unique
class CandidateChange(StrEnum):
    """Whether a complete candidate exists and differs from its baseline."""

    CHANGED = "changed"
    UNCHANGED = "unchanged"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """Safe deterministic summary with no locators or credentials."""

    exit_code: CollectionExitCode
    candidate_change: CandidateChange
    digest: str | None
    succeeded_accounts: int
    failed_accounts: int
    failed_account_ids: tuple[AccountId, ...]


def aborted_result(exit_code: CollectionExitCode) -> CollectionResult:
    """Build a no-candidate terminal result."""
    return CollectionResult(exit_code, CandidateChange.ABSENT, None, 0, 0, ())
