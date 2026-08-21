from __future__ import annotations

__test__ = False

__all__ = (
    "UTC",
    "_NOW",
    "AcceptedSnapshotBatchFailure",
    "Account",
    "AccountIdentityConflictError",
    "AccountInput",
    "AccountKind",
    "AccountRouteFailed",
    "AccountRouteFailureCategory",
    "AdapterBatch",
    "AdapterInstanceOrdinal",
    "AdapterOperation",
    "AdapterPostLocatorBatch",
    "AdapterPostLocatorRequest",
    "AdapterPostRequest",
    "AdapterRequestError",
    "AdapterRequestErrorCategory",
    "BatchCompleted",
    "BrightDataAdapterConfig",
    "BrightDataCompanyIdentity",
    "BrightDataError",
    "BrightDataErrorCategory",
    "BrightDataLinkedInAdapter",
    "BrightDataPersonIdentity",
    "BrightDataPost",
    "BrightDataPostBatchResult",
    "CollectedAccount",
    "InstanceHealthStatus",
    "InvalidCredentialBatchFailure",
    "LocatorPostsBatchCompleted",
    "Platform",
    "PlatformAccountId",
    "QuotaBatchFailure",
    "ResolvedAccountIdentity",
    "ResolvedAdapterDrivers",
    "ResolvedLocatorPosts",
    "RetryableBatchFailure",
    "RouterDiagnosticCategory",
    "RouterRunStatus",
    "SchemaBatchFailure",
    "SecretStr",
    "SyntheticBrightDataClient",
    "UnresolvedAccountIdentity",
    "UnresolvedLocatorPosts",
    "_account",
    "_post",
    "account_id_for",
    "bootstrap_runtime",
    "date",
    "datetime",
    "parse_linkedin_locator",
    "pytest",
)


from datetime import UTC, date, datetime

import pytest
from pydantic import SecretStr

from social_media_subscriber.accounts.identity import AccountIdentityConflictError
from social_media_subscriber.accounts.input import AccountInput
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters import AdapterOperation, ResolvedAdapterDrivers
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AdapterBatch,
    AdapterInstanceOrdinal,
    AdapterPostLocatorBatch,
    AdapterPostLocatorRequest,
    AdapterPostRequest,
    AdapterRequestError,
    AdapterRequestErrorCategory,
    BatchCompleted,
    CollectedAccount,
    InvalidCredentialBatchFailure,
    LocatorPostsBatchCompleted,
    QuotaBatchFailure,
    ResolvedLocatorPosts,
    RetryableBatchFailure,
    SchemaBatchFailure,
    UnresolvedLocatorPosts,
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
from social_media_subscriber.domain.ids import PlatformAccountId, account_id_for
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
from social_media_subscriber.providers.brightdata.models import (
    BrightDataCompanyIdentity,
    BrightDataPersonIdentity,
    BrightDataPost,
)
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    ResolvedAccountIdentity,
    UnresolvedAccountIdentity,
)
from tests.fakes.brightdata_adapter import SyntheticBrightDataClient

_NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _account(kind: AccountKind, platform_id: str, slug: str) -> Account:
    stable_id = PlatformAccountId(platform_id)
    path = "in" if kind is AccountKind.PERSON else "company"
    return Account(
        id=account_id_for(kind, stable_id),
        platform=Platform.LINKEDIN,
        kind=kind,
        platform_account_id=stable_id,
        profile_url=f"https://www.linkedin.com/{path}/{slug}/",
        url_aliases=(),
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
        "user_id": account.platform_account_id,
        "user_url": account.profile_url,
    }
    if images:
        payload["images"] = ["https://media.licdn.com/image.png"]
    if provider_note is not None:
        payload["provider_note"] = provider_note
    return BrightDataPost.model_validate(payload)
