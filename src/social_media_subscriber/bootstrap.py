"""Explicit production registry and credential-bound Adapter bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from social_media_subscriber.adapters.instance import AdapterInstanceSpec
from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.providers.apify.adapter import (
    ApifyAdapterConfig,
    ApifyAdapterFactory,
    ApifyClientContract,
    ApifyLinkedInAdapter,
)
from social_media_subscriber.providers.apify.client import ApifyClient
from social_media_subscriber.providers.apify.x_adapter import (
    ApifyXAdapter,
    ApifyXAdapterFactory,
    ApifyXClientContract,
)
from social_media_subscriber.providers.apify.x_client import ApifyXClient
from social_media_subscriber.providers.brightdata.adapter import (
    BrightDataAdapterFactory,
    BrightDataLinkedInAdapter,
)
from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.runtime_input import SourceId, SourceInput

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataAdapterConfig,
        BrightDataClientContract,
    )
    from social_media_subscriber.runtime_input import RuntimeInput


class SourceComposer(Protocol):
    """Map one configured source to explicit adapter instances."""

    def build_specs(self, source: SourceInput) -> tuple[AdapterInstanceSpec, ...]:
        """Build ordered instances supported by this source provider."""
        ...


@dataclass(frozen=True, slots=True)
class BrightDataSourceComposer:
    """Compose the explicitly supported Bright Data driver set."""

    factory: BrightDataAdapterFactory

    def build_specs(self, source: SourceInput) -> tuple[AdapterInstanceSpec, ...]:
        """Bind one configured Bright Data credential to LinkedIn Posts."""
        return (
            AdapterInstanceSpec(
                BrightDataLinkedInAdapter,
                self.factory,
                source.credential,
            ),
        )


@dataclass(frozen=True, slots=True)
class ApifySourceComposer:
    """Compose the explicitly supported Apify driver set."""

    linkedin_factory: ApifyAdapterFactory
    x_factory: ApifyXAdapterFactory

    def build_specs(self, source: SourceInput) -> tuple[AdapterInstanceSpec, ...]:
        """Bind one configured Apify credential to every supported platform."""
        return (
            AdapterInstanceSpec(
                ApifyLinkedInAdapter,
                self.linkedin_factory,
                source.credential,
            ),
            AdapterInstanceSpec(
                ApifyXAdapter,
                self.x_factory,
                source.credential,
            ),
        )


@dataclass(frozen=True, slots=True)
class SubscriberRuntime:
    """Router and owned adapter resources for one collection run."""

    router: Router

    async def aclose(self) -> None:
        """Close all resources created for this runtime."""
        await self.router.aclose()


def _build_client(credential: str) -> BrightDataClient:
    return BrightDataClient(credential)


def _build_apify_client(credential: str) -> ApifyClient:
    return ApifyClient(credential)


def _build_apify_x_client(credential: str) -> ApifyXClient:
    return ApifyXClient(credential)


def build_runtime(
    registry: AdapterRegistry,
    instance_specs: tuple[AdapterInstanceSpec, ...],
) -> SubscriberRuntime:
    """Build a provider-neutral runtime from ordered instances."""
    return SubscriberRuntime(Router(registry, instance_specs))


def compose_runtime(
    runtime_input: RuntimeInput,
    composers: Mapping[SourceId, SourceComposer],
) -> SubscriberRuntime:
    """Compose ordered sources through an explicit provider allowlist."""
    drivers: list[type[AdapterDriver]] = []
    specs: list[AdapterInstanceSpec] = []
    for source in runtime_input.sources:
        composer = composers.get(source.source_id)
        if composer is None:
            message = f"unsupported source composer: {source.source_id.value}"
            raise ValueError(message)
        source_specs = composer.build_specs(source)
        for spec in source_specs:
            if spec.driver_class not in drivers:
                drivers.append(spec.driver_class)
        specs.extend(source_specs)
    return build_runtime(AdapterRegistry(tuple(drivers)), tuple(specs))


def bootstrap_runtime(
    runtime_input: RuntimeInput,
    config: BrightDataAdapterConfig,
    *,
    client_builder: Callable[[str], BrightDataClientContract] = _build_client,
    apify_client_builder: Callable[[str], ApifyClientContract] = _build_apify_client,
    apify_x_client_builder: Callable[
        [str], ApifyXClientContract
    ] = _build_apify_x_client,
) -> SubscriberRuntime:
    """Compose production sources with Apify ahead of Bright Data."""
    brightdata_factory = BrightDataAdapterFactory(config, client_builder)
    apify_factory = ApifyAdapterFactory(
        ApifyAdapterConfig(config.first_seen_at), apify_client_builder
    )
    apify_x_factory = ApifyXAdapterFactory(
        ApifyAdapterConfig(config.first_seen_at), apify_x_client_builder
    )
    prioritized_input = runtime_input.model_copy(
        update={
            "sources": tuple(
                sorted(
                    runtime_input.sources,
                    key=lambda source: source.source_id is not SourceId.APIFY,
                )
            )
        }
    )
    return compose_runtime(
        prioritized_input,
        {
            SourceId.APIFY: ApifySourceComposer(apify_factory, apify_x_factory),
            SourceId.BRIGHTDATA: BrightDataSourceComposer(brightdata_factory),
        },
    )
