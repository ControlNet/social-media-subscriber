from __future__ import annotations

import copy
import os
import socket
from typing import ClassVar

import pytest

from social_media_subscriber.adapters.metadata import AdapterMetadata, adapter
from social_media_subscriber.adapters.metadata_errors import MetadataViolation
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.protocol import AdapterDriver
from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.adapters.registry_errors import (
    DuplicateAdapterDriverError,
    InvalidAdapterMetadataError,
    MissingAdapterMetadataError,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.brightdata.adapter import (
    BrightDataLinkedInAdapter,
)


class _DeclaredAdapterDriver(AdapterDriver):
    adapter_metadata: ClassVar[AdapterMetadata]


def test_registry_preserves_explicit_order_when_resolving_capability() -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class FirstDriver(_DeclaredAdapterDriver):
        pass

    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON, AccountKind.COMPANY),
        supports_batch=True,
    )
    class SecondDriver(_DeclaredAdapterDriver):
        pass

    registry = AdapterRegistry((SecondDriver, FirstDriver))

    # When
    result = registry.resolve(
        platform=Platform.LINKEDIN,
        operation=AdapterOperation.COLLECT_ACCOUNT_POSTS,
        account_kind=AccountKind.PERSON,
    )

    # Then
    assert result == (SecondDriver, FirstDriver)
    assert registry.driver_classes == (SecondDriver, FirstDriver)


def test_registry_returns_empty_tuple_for_unsupported_capability() -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class PersonPostsDriver(_DeclaredAdapterDriver):
        pass

    registry = AdapterRegistry((PersonPostsDriver,))

    # When
    result = registry.resolve(
        platform=Platform.LINKEDIN,
        operation=AdapterOperation.COLLECT_ACCOUNT_POSTS,
        account_kind=AccountKind.COMPANY,
    )

    # Then
    assert result == ()


def test_production_registry_exposes_only_posts_for_supported_kinds() -> None:
    # Given
    metadata = BrightDataLinkedInAdapter.adapter_metadata
    registry = AdapterRegistry((BrightDataLinkedInAdapter,))

    # When
    resolutions = tuple(
        registry.resolve(
            platform=Platform.LINKEDIN,
            operation=AdapterOperation.COLLECT_ACCOUNT_POSTS,
            account_kind=account_kind,
        )
        for account_kind in (AccountKind.PERSON, AccountKind.COMPANY)
    )

    # Then
    assert registry.driver_classes == (BrightDataLinkedInAdapter,)
    assert metadata.operations == (AdapterOperation.COLLECT_ACCOUNT_POSTS,)
    assert metadata.account_kinds == (AccountKind.PERSON, AccountKind.COMPANY)
    assert metadata.supports_batch is True
    assert resolutions == (
        (BrightDataLinkedInAdapter,),
        (BrightDataLinkedInAdapter,),
    )


def test_registry_rejects_undecorated_driver_before_instance_creation() -> None:
    # Given
    class UndecoratedDriver(_DeclaredAdapterDriver):
        instances_created: ClassVar[int] = 0

        def __init__(self) -> None:
            type(self).instances_created += 1

    # When
    with pytest.raises(MissingAdapterMetadataError) as captured:
        _ = AdapterRegistry((UndecoratedDriver,))

    # Then
    assert captured.value.driver_name == "UndecoratedDriver"
    assert UndecoratedDriver.instances_created == 0


def test_registry_rejects_repeated_driver_identity() -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class RepeatedDriver(_DeclaredAdapterDriver):
        pass

    # When
    with pytest.raises(DuplicateAdapterDriverError) as captured:
        _ = AdapterRegistry((RepeatedDriver, RepeatedDriver))

    # Then
    assert captured.value.driver_name == "RepeatedDriver"
    assert captured.value.first_index == 0
    assert captured.value.duplicate_index == 1


def test_registry_allows_multiple_drivers_for_the_same_capability() -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=True,
    )
    class FirstDriver(_DeclaredAdapterDriver):
        pass

    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=True,
    )
    class DuplicateDescriptorDriver(_DeclaredAdapterDriver):
        pass

    registry = AdapterRegistry((FirstDriver, DuplicateDescriptorDriver))

    # When
    result = registry.resolve(
        platform=Platform.LINKEDIN,
        operation=AdapterOperation.COLLECT_ACCOUNT_POSTS,
        account_kind=AccountKind.PERSON,
    )

    # Then
    assert result == (FirstDriver, DuplicateDescriptorDriver)


@pytest.mark.parametrize(
    ("operations", "account_kinds", "violation"),
    [
        ((), (AccountKind.PERSON,), MetadataViolation.EMPTY_OPERATIONS),
        (
            (AdapterOperation.COLLECT_ACCOUNT_POSTS,),
            (),
            MetadataViolation.EMPTY_ACCOUNT_KINDS,
        ),
        (
            (
                AdapterOperation.COLLECT_ACCOUNT_POSTS,
                AdapterOperation.COLLECT_ACCOUNT_POSTS,
            ),
            (AccountKind.PERSON,),
            MetadataViolation.DUPLICATE_OPERATIONS,
        ),
        (
            (AdapterOperation.COLLECT_ACCOUNT_POSTS,),
            (AccountKind.PERSON, AccountKind.PERSON),
            MetadataViolation.DUPLICATE_ACCOUNT_KINDS,
        ),
    ],
)
def test_registry_rejects_invalid_metadata(
    operations: tuple[AdapterOperation, ...],
    account_kinds: tuple[AccountKind, ...],
    violation: MetadataViolation,
) -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=operations,
        account_kinds=account_kinds,
        supports_batch=False,
    )
    class InvalidMetadataDriver(_DeclaredAdapterDriver):
        pass

    # When
    with pytest.raises(InvalidAdapterMetadataError) as captured:
        _ = AdapterRegistry((InvalidMetadataDriver,))

    # Then
    assert captured.value.driver_name == "InvalidMetadataDriver"
    assert captured.value.violation is violation


def test_registry_rejects_non_enum_platform_before_instance_creation() -> None:
    # Given
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class StringPlatformDriver(_DeclaredAdapterDriver):
        instances_created: ClassVar[int] = 0

        def __init__(self) -> None:
            type(self).instances_created += 1

    malformed_metadata = copy.replace(
        StringPlatformDriver.adapter_metadata,
        platform="linkedin",
    )
    metadata_attribute = "adapter_metadata"
    setattr(StringPlatformDriver, metadata_attribute, malformed_metadata)

    # When
    with pytest.raises(InvalidAdapterMetadataError) as captured:
        _ = AdapterRegistry((StringPlatformDriver,))

    # Then
    assert captured.value.driver_name == "StringPlatformDriver"
    assert captured.value.violation is MetadataViolation.INVALID_PLATFORM
    assert StringPlatformDriver.instances_created == 0


def test_registry_rejects_non_metadata_descriptor_before_instance_creation() -> None:
    # Given
    class MalformedDescriptorDriver(_DeclaredAdapterDriver):
        instances_created: ClassVar[int] = 0

        def __init__(self) -> None:
            type(self).instances_created += 1

    metadata_attribute = "adapter_metadata"
    setattr(MalformedDescriptorDriver, metadata_attribute, "malformed")

    # When
    with pytest.raises(InvalidAdapterMetadataError) as captured:
        _ = AdapterRegistry((MalformedDescriptorDriver,))

    # Then
    assert captured.value.driver_name == "MalformedDescriptorDriver"
    assert captured.value.violation is MetadataViolation.MALFORMED_METADATA
    assert MalformedDescriptorDriver.instances_created == 0


def test_registry_construction_has_no_external_or_instance_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    def fail_environment_read(name: str, default: str | None = None) -> str | None:
        pytest.fail(f"unexpected environment read: {name!r}, {default!r}")

    def fail_network_call(
        address: tuple[str, int],
        timeout: float | None = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        pytest.fail(
            f"unexpected network call: {address!r}, {timeout!r}, {source_address!r}"
        )

    monkeypatch.setattr(os, "getenv", fail_environment_read)
    monkeypatch.setattr(socket, "create_connection", fail_network_call)

    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.COMPANY,),
        supports_batch=True,
    )
    class SideEffectDriver(_DeclaredAdapterDriver):
        instances_created: ClassVar[int] = 0

        def __init__(self) -> None:
            type(self).instances_created += 1

    # When
    registry = AdapterRegistry((SideEffectDriver,))

    # Then
    assert registry.driver_classes == (SideEffectDriver,)
    assert SideEffectDriver.instances_created == 0
