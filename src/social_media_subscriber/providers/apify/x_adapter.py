"""Credential-bound Apify X adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, final

from social_media_subscriber.adapters import AdapterMetadata, AdapterOperation, adapter
from social_media_subscriber.adapters.instance import (
    AdapterInstanceOrdinal,
    BatchCompleted,
    CollectedAccount,
    SchemaBatchFailure,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.apify.adapter import (
    ApifyAdapterConfig,
    map_apify_error,
)
from social_media_subscriber.providers.apify.errors import ApifyError
from social_media_subscriber.providers.apify.x_normalize import normalize_posts
from social_media_subscriber.providers.apify.x_requests import ApifyXPostInput

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr

    from social_media_subscriber.adapters.instance import (
        AdapterAttempt,
        AdapterBatch,
        AdapterInstance,
    )
    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.providers.apify.x_models import ApifyXPost


class ApifyXClientContract(Protocol):
    """Structural Xquik client surface used by the adapter."""

    async def aclose(self) -> None:
        """Close the credential-bound transport."""
        ...

    async def collect_posts(self, request: ApifyXPostInput) -> tuple[ApifyXPost, ...]:
        """Collect one X profile's complete requested post window."""
        ...


class _DeclaredAdapter:
    adapter_metadata: ClassVar[AdapterMetadata]


@final
@adapter(
    platform=Platform.X,
    operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
    account_kinds=(AccountKind.PROFILE,),
    supports_batch=False,
)
class ApifyXAdapter(_DeclaredAdapter):
    """One credential-bound Xquik profile-with-replies Actor."""

    def __init__(
        self,
        client: ApifyXClientContract,
        ordinal: AdapterInstanceOrdinal,
        config: ApifyAdapterConfig,
    ) -> None:
        """Bind one client and run timestamp to an opaque ordinal."""
        self._client = client
        self.ordinal = ordinal
        self.driver_class: type[AdapterDriver] = ApifyXAdapter
        self._config = config

    async def aclose(self) -> None:
        """Close the credential-bound provider client."""
        await self._client.aclose()

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        """Collect one X profile and map failures into router outcomes."""
        if len(batch.requests) != 1:
            return SchemaBatchFailure()
        request = batch.requests[0]
        try:
            records = await self._client.collect_posts(
                ApifyXPostInput(
                    request.account.profile_url,
                    request.start_date,
                    request.end_date,
                    request.is_initial_collection,
                )
            )
            posts = normalize_posts(
                request.account, records, self._config.first_seen_at
            )
        except ApifyError as error:
            return map_apify_error(error)
        return BatchCompleted((CollectedAccount(request.account.id, posts),))


@final
@dataclass(frozen=True, slots=True)
class ApifyXAdapterFactory:
    """Create one Xquik adapter for each configured credential."""

    config: ApifyAdapterConfig
    client_builder: Callable[[str], ApifyXClientContract]

    def create(
        self, credential: SecretStr, ordinal: AdapterInstanceOrdinal
    ) -> AdapterInstance:
        """Bind one approved credential to an Xquik adapter instance."""
        return ApifyXAdapter(
            self.client_builder(credential.get_secret_value()), ordinal, self.config
        )
