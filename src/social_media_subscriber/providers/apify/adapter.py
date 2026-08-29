"""Credential-bound Apify LinkedIn adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, final

from social_media_subscriber.adapters import (
    AdapterMetadata,
    AdapterOperation,
    adapter,
)
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AdapterInstanceOrdinal,
    BatchCompleted,
    CollectedAccount,
    InvalidCredentialBatchFailure,
    QuotaBatchFailure,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.normalize import normalize_posts
from social_media_subscriber.providers.apify.requests import ApifyPostInput

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from pydantic import SecretStr

    from social_media_subscriber.adapters.instance import (
        AdapterAttempt,
        AdapterBatch,
        AdapterInstance,
    )
    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.providers.apify.models import ApifyPost


class ApifyClientContract(Protocol):
    """Structural client surface used by the adapter."""

    async def aclose(self) -> None:
        """Close the credential-bound transport."""
        ...

    async def collect_posts(self, request: ApifyPostInput) -> tuple[ApifyPost, ...]:
        """Collect one account's complete requested post window."""
        ...


@dataclass(frozen=True, slots=True)
class ApifyAdapterConfig:
    """Run-scoped deterministic values shared by Apify instances."""

    first_seen_at: datetime


class _DeclaredAdapter:
    adapter_metadata: ClassVar[AdapterMetadata]


@final
@adapter(
    platform=Platform.LINKEDIN,
    operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
    account_kinds=(AccountKind.PERSON, AccountKind.COMPANY),
    supports_batch=False,
)
class ApifyLinkedInAdapter(_DeclaredAdapter):
    """One credential-bound Apify LinkedIn profile-post Actor."""

    def __init__(
        self,
        client: ApifyClientContract,
        ordinal: AdapterInstanceOrdinal,
        config: ApifyAdapterConfig,
    ) -> None:
        """Bind one client and run timestamp to an opaque ordinal."""
        self._client = client
        self.ordinal = ordinal
        self.driver_class: type[AdapterDriver] = ApifyLinkedInAdapter
        self._config = config

    async def aclose(self) -> None:
        """Close the credential-bound provider client."""
        await self._client.aclose()

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        """Collect one account and map failures into router outcomes."""
        if len(batch.requests) != 1:
            return SchemaBatchFailure()
        request = batch.requests[0]
        try:
            records = await self._client.collect_posts(
                ApifyPostInput(
                    request.account.profile_url, request.start_date, request.end_date
                )
            )
            posts = normalize_posts(
                request.account, records, self._config.first_seen_at
            )
        except ApifyError as error:
            return map_apify_error(error)
        return BatchCompleted((CollectedAccount(request.account.id, posts),))


def map_apify_error(error: ApifyError) -> AdapterAttempt:
    """Map one sanitized Apify error into router control flow."""
    if error.run_accepted:
        return AcceptedSnapshotBatchFailure()
    match error.category:
        case ApifyErrorCategory.AUTH:
            return InvalidCredentialBatchFailure()
        case ApifyErrorCategory.QUOTA:
            return QuotaBatchFailure()
        case ApifyErrorCategory.RETRYABLE | ApifyErrorCategory.TIMEOUT:
            return RetryableBatchFailure()
        case (
            ApifyErrorCategory.INPUT
            | ApifyErrorCategory.RUN_TERMINAL
            | ApifyErrorCategory.SCHEMA
            | ApifyErrorCategory.OWNERSHIP
            | ApifyErrorCategory.DUPLICATE
            | ApifyErrorCategory.POST_URL
            | ApifyErrorCategory.INCOMPLETE
        ):
            return SchemaBatchFailure()


@final
@dataclass(frozen=True, slots=True)
class ApifyAdapterFactory:
    """Create one Apify adapter for each configured credential."""

    config: ApifyAdapterConfig
    client_builder: Callable[[str], ApifyClientContract]

    def create(
        self, credential: SecretStr, ordinal: AdapterInstanceOrdinal
    ) -> AdapterInstance:
        """Bind one approved credential to an Apify adapter instance."""
        return ApifyLinkedInAdapter(
            self.client_builder(credential.get_secret_value()), ordinal, self.config
        )
