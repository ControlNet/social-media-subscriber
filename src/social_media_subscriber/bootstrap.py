"""Explicit production registry and credential-bound Adapter bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.providers.brightdata.adapter import (
    BrightDataAdapterFactory,
    BrightDataLinkedInAdapter,
)
from social_media_subscriber.providers.brightdata.client import BrightDataClient

if TYPE_CHECKING:
    from collections.abc import Callable

    from social_media_subscriber.accounts.input import AccountInput
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataAdapterConfig,
        BrightDataClientContract,
    )


EXPLICIT_ADAPTER_REGISTRY = AdapterRegistry((BrightDataLinkedInAdapter,))


@dataclass(frozen=True, slots=True)
class SubscriberRuntime:
    """Explicit registry and Router created from parsed runtime input."""

    registry: AdapterRegistry
    router: Router


def _build_client(credential: str) -> BrightDataClient:
    return BrightDataClient(credential)


def bootstrap_runtime(
    account_input: AccountInput,
    config: BrightDataAdapterConfig,
    *,
    client_builder: Callable[[str], BrightDataClientContract] = _build_client,
) -> SubscriberRuntime:
    """Construct exactly one approved Adapter instance per parsed credential."""
    factory = BrightDataAdapterFactory(config, client_builder)
    return SubscriberRuntime(
        EXPLICIT_ADAPTER_REGISTRY,
        Router(
            EXPLICIT_ADAPTER_REGISTRY,
            factory,
            account_input.bright_data_api_keys,
        ),
    )
