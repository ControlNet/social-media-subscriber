"""Class-level capability metadata and its side-effect-free decorator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from social_media_subscriber.adapters.operations import AdapterOperation
    from social_media_subscriber.domain.platform import AccountKind, Platform


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    """Immutable capabilities declared by an adapter driver class."""

    platform: Platform
    operations: tuple[AdapterOperation, ...]
    account_kinds: tuple[AccountKind, ...]
    supports_batch: bool


class AdapterMetadataOwner(Protocol):
    """Class metadata member implemented by decorated adapter drivers."""

    adapter_metadata: ClassVar[AdapterMetadata]


DriverT = TypeVar("DriverT", bound=AdapterMetadataOwner)


def adapter(
    *,
    platform: Platform,
    operations: tuple[AdapterOperation, ...],
    account_kinds: tuple[AccountKind, ...],
    supports_batch: bool,
) -> Callable[[type[DriverT]], type[DriverT]]:
    """Attach immutable capability metadata without constructing the driver."""
    metadata = AdapterMetadata(
        platform=platform,
        operations=operations,
        account_kinds=account_kinds,
        supports_batch=supports_batch,
    )

    def decorate(driver_class: type[DriverT]) -> type[DriverT]:
        driver_class.adapter_metadata = metadata
        return driver_class

    return decorate
