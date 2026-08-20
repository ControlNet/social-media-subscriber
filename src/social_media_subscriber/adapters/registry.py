"""Immutable explicit registration and deterministic capability lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from social_media_subscriber.adapters.metadata_errors import MetadataViolation
from social_media_subscriber.adapters.registry_errors import (
    DuplicateAdapterDescriptorError,
    DuplicateAdapterDriverError,
    InvalidAdapterMetadataError,
    MissingAdapterMetadataError,
)

if TYPE_CHECKING:
    from social_media_subscriber.adapters.metadata import AdapterMetadata
    from social_media_subscriber.adapters.operations import AdapterOperation
    from social_media_subscriber.adapters.protocol import AdapterDriver
    from social_media_subscriber.domain.platform import AccountKind, Platform


@dataclass(frozen=True, slots=True)
class ResolvedAdapterDrivers:
    """Ordered driver classes matching one requested capability."""

    driver_classes: tuple[type[AdapterDriver], ...]


@dataclass(frozen=True, slots=True)
class UnsupportedAdapterCapability:
    """Typed outcome for a capability absent from the explicit registry."""

    platform: Platform
    operation: AdapterOperation
    account_kind: AccountKind


type AdapterResolution = ResolvedAdapterDrivers | UnsupportedAdapterCapability


def _find_metadata_violation(metadata: AdapterMetadata) -> MetadataViolation | None:
    if not metadata.operations:
        return MetadataViolation.EMPTY_OPERATIONS
    if not metadata.account_kinds:
        return MetadataViolation.EMPTY_ACCOUNT_KINDS
    if len(frozenset(metadata.operations)) != len(metadata.operations):
        return MetadataViolation.DUPLICATE_OPERATIONS
    if len(frozenset(metadata.account_kinds)) != len(metadata.account_kinds):
        return MetadataViolation.DUPLICATE_ACCOUNT_KINDS
    return None


@dataclass(frozen=True, slots=True)
class AdapterRegistry:
    """Immutable, explicitly ordered registry of approved adapter drivers."""

    driver_classes: tuple[type[AdapterDriver], ...]

    def __post_init__(self) -> None:
        """Reject invalid entries before the registry becomes observable."""
        seen_drivers: dict[type[AdapterDriver], int] = {}
        seen_descriptors: dict[AdapterMetadata, tuple[str, AdapterMetadata]] = {}

        for index, driver_class in enumerate(self.driver_classes):
            first_index = seen_drivers.get(driver_class)
            if first_index is not None:
                raise DuplicateAdapterDriverError(
                    driver_name=driver_class.__name__,
                    first_index=first_index,
                    duplicate_index=index,
                )

            try:
                metadata = driver_class.adapter_metadata
            except AttributeError:
                raise MissingAdapterMetadataError(
                    driver_name=driver_class.__name__
                ) from None

            violation = _find_metadata_violation(metadata)
            if violation is not None:
                raise InvalidAdapterMetadataError(
                    driver_name=driver_class.__name__,
                    violation=violation,
                )

            duplicate_descriptor = seen_descriptors.get(metadata)
            if duplicate_descriptor is not None:
                first_driver_name, first_metadata = duplicate_descriptor
                raise DuplicateAdapterDescriptorError(
                    first_driver_name=first_driver_name,
                    duplicate_driver_name=driver_class.__name__,
                    metadata=first_metadata,
                )

            seen_drivers[driver_class] = index
            seen_descriptors[metadata] = (driver_class.__name__, metadata)

    def resolve(
        self,
        *,
        platform: Platform,
        operation: AdapterOperation,
        account_kind: AccountKind,
    ) -> AdapterResolution:
        """Resolve matching drivers in the registry's declared order."""
        matches = tuple(
            driver_class
            for driver_class in self.driver_classes
            if driver_class.adapter_metadata.platform is platform
            and operation in driver_class.adapter_metadata.operations
            and account_kind in driver_class.adapter_metadata.account_kinds
        )
        if matches:
            return ResolvedAdapterDrivers(driver_classes=matches)
        return UnsupportedAdapterCapability(
            platform=platform,
            operation=operation,
            account_kind=account_kind,
        )
