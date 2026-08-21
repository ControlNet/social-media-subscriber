from __future__ import annotations

__test__ = False

__all__ = (
    "UTC",
    "_NOW",
    "AcceptedSnapshotBatchFailure",
    "Account",
    "AccountInput",
    "AccountKind",
    "AccountRouteFailed",
    "AccountRouteFailureCategory",
    "AdapterBatch",
    "AdapterInstanceOrdinal",
    "AdapterOperation",
    "AdapterPostRequest",
    "AdapterRequestError",
    "AdapterRequestErrorCategory",
    "BatchCompleted",
    "BrightDataAdapterConfig",
    "BrightDataError",
    "BrightDataErrorCategory",
    "BrightDataLinkedInAdapter",
    "BrightDataPost",
    "BrightDataPostBatchResult",
    "CollectedAccount",
    "InstanceHealthStatus",
    "InvalidCredentialBatchFailure",
    "Platform",
    "QuotaBatchFailure",
    "ResolvedAdapterDrivers",
    "RetryableBatchFailure",
    "RouterDiagnosticCategory",
    "RouterRunStatus",
    "SchemaBatchFailure",
    "SecretStr",
    "SyntheticBrightDataClient",
    "_account",
    "_post",
    "bootstrap_runtime",
    "date",
    "datetime",
    "parse_linkedin_locator",
    "pytest",
)


from datetime import UTC, date, datetime

import pytest
from pydantic import SecretStr

from social_media_subscriber.accounts.input import AccountInput
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters import AdapterOperation, ResolvedAdapterDrivers
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AdapterBatch,
    AdapterInstanceOrdinal,
    AdapterPostRequest,
    AdapterRequestError,
    AdapterRequestErrorCategory,
    BatchCompleted,
    CollectedAccount,
    InvalidCredentialBatchFailure,
    QuotaBatchFailure,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.adapters.router_outcomes import (
    AccountRouteFailed,
    AccountRouteFailureCategory,
    InstanceHealthStatus,
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.bootstrap import bootstrap_runtime
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.brightdata.adapter import (
    BrightDataLinkedInAdapter,
)
from social_media_subscriber.providers.brightdata.adapter_contracts import (
    BrightDataAdapterConfig,
    BrightDataPostBatchResult,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from tests.fakes.brightdata_adapter import SyntheticBrightDataClient

_NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _account(kind: AccountKind, slug: str) -> Account:
    path = "in" if kind is AccountKind.PERSON else "company"
    profile_url = f"https://www.linkedin.com/{path}/{slug}/"
    return Account(
        id=AccountId(profile_url),
        platform=Platform.LINKEDIN,
        kind=kind,
        profile_url=profile_url,
        first_seen_at=_NOW,
    )


def _post(
    account: Account,
    post_id: str,
    post_type: str = "post",
    *,
    images: bool = False,
    provider_note: str | None = None,
) -> BrightDataPost:
    payload: dict[str, str | list[str]] = {
        "id": post_id,
        "date_posted": "2026-08-20T12:00:00+00:00",
        "post_type": post_type,
        "url": f"https://www.linkedin.com/posts/{post_id}",
        "user_id": "synthetic-provider-user",
        "user_url": account.profile_url,
    }
    if images:
        payload["images"] = ["https://media.licdn.com/image.png"]
    if provider_note is not None:
        payload["provider_note"] = provider_note
    return BrightDataPost.model_validate(payload)
