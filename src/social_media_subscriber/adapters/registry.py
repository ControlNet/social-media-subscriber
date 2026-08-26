"""Immutable explicit registration and deterministic capability lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from social_media_subscriber.adapters.metadata import AdapterMetadata
from social_media_subscriber.adapters.metadata_errors import MetadataViolation
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.registry_errors import (
    DuplicateAdapterDriverError,
    InvalidAdapterMetadataError,
    MissingAdapterMetadataError,
)
from social_media_subscriber.domain.platform import (
    AccountKind,
    Platform,
    supported_account_kinds,
)

if TYPE_CHECKING:
    from social_media_subscriber.adapters.protocol import AdapterDriver


def _find_metadata_violation(metadata: AdapterMetadata) -> MetadataViolation | None:
    shape_checks = (
        (type(metadata.platform) is not Platform, MetadataViolation.INVALID_PLATFORM),
        (
            type(metadata.operations) is not tuple
            or any(
                type(operation) is not AdapterOperation
                for operation in metadata.operations
            ),
            MetadataViolation.INVALID_OPERATIONS,
        ),
        (
            type(metadata.account_kinds) is not tuple
            or any(
                type(account_kind) is not AccountKind
                for account_kind in metadata.account_kinds
            ),
            MetadataViolation.INVALID_ACCOUNT_KINDS,
        ),
        (
            type(metadata.supports_batch) is not bool,
            MetadataViolation.INVALID_SUPPORTS_BATCH,
        ),
    )
    for is_invalid, violation in shape_checks:
        if is_invalid:
            return violation

    content_checks = (
        (not metadata.operations, MetadataViolation.EMPTY_OPERATIONS),
        (not metadata.account_kinds, MetadataViolation.EMPTY_ACCOUNT_KINDS),
        (
            len(frozenset(metadata.operations)) != len(metadata.operations),
            MetadataViolation.DUPLICATE_OPERATIONS,
        ),
        (
            len(frozenset(metadata.account_kinds)) != len(metadata.account_kinds),
            MetadataViolation.DUPLICATE_ACCOUNT_KINDS,
        ),
        (
            not frozenset(metadata.account_kinds).issubset(
                supported_account_kinds(metadata.platform)
            ),
            MetadataViolation.INVALID_ACCOUNT_KINDS,
        ),
    )
    for is_invalid, violation in content_checks:
        if is_invalid:
            return violation
    return None


@dataclass(frozen=True, slots=True)
class AdapterRegistry:
    """Immutable, explicitly ordered registry of approved adapter drivers."""

    driver_classes: tuple[type[AdapterDriver], ...]

    def __post_init__(self) -> None:
        """Reject invalid entries before the registry becomes observable."""
        seen_drivers: dict[type[AdapterDriver], int] = {}
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

            if type(metadata) is not AdapterMetadata:
                raise InvalidAdapterMetadataError(
                    driver_name=driver_class.__name__,
                    violation=MetadataViolation.MALFORMED_METADATA,
                )

            violation = _find_metadata_violation(metadata)
            if violation is not None:
                raise InvalidAdapterMetadataError(
                    driver_name=driver_class.__name__,
                    violation=violation,
                )

            seen_drivers[driver_class] = index

    def resolve(
        self,
        *,
        platform: Platform,
        operation: AdapterOperation,
        account_kind: AccountKind,
    ) -> tuple[type[AdapterDriver], ...]:
        """Resolve matching drivers in the registry's declared order."""
        return tuple(
            driver_class
            for driver_class in self.driver_classes
            if driver_class.adapter_metadata.platform is platform
            and operation in driver_class.adapter_metadata.operations
            and account_kind in driver_class.adapter_metadata.account_kinds
        )
