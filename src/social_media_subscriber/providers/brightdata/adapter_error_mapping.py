"""Router-facing classification of Bright Data provider failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AccountRejectionCategory,
    BatchCompleted,
    InvalidCredentialBatchFailure,
    QuotaBatchFailure,
    RejectedAccount,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)

if TYPE_CHECKING:
    from social_media_subscriber.adapters.instance import (
        AdapterAttempt,
        AdapterBatch,
    )


def map_provider_error(batch: AdapterBatch, error: BrightDataError) -> AdapterAttempt:
    """Classify a provider failure from known-Account Post collection."""
    if error.snapshot_accepted:
        return AcceptedSnapshotBatchFailure()
    match error.category:
        case BrightDataErrorCategory.AUTH:
            result: AdapterAttempt = InvalidCredentialBatchFailure()
        case BrightDataErrorCategory.QUOTA:
            result = QuotaBatchFailure()
        case BrightDataErrorCategory.NOT_FOUND:
            result = BatchCompleted(
                tuple(
                    RejectedAccount(account.id, AccountRejectionCategory.NOT_FOUND)
                    for account in batch.accounts
                )
            )
        case BrightDataErrorCategory.INPUT:
            result = BatchCompleted(
                tuple(
                    RejectedAccount(account.id, AccountRejectionCategory.INVALID)
                    for account in batch.accounts
                )
            )
        case BrightDataErrorCategory.RETRYABLE | BrightDataErrorCategory.TIMEOUT:
            result = RetryableBatchFailure()
        case (
            BrightDataErrorCategory.SNAPSHOT_TIMEOUT
            | BrightDataErrorCategory.SNAPSHOT_TERMINAL
            | BrightDataErrorCategory.SCHEMA
        ):
            result = SchemaBatchFailure()
    return result
