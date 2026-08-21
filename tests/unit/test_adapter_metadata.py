from __future__ import annotations

import importlib
import os
import socket
import sys
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING, ClassVar, override

import pytest

from social_media_subscriber.adapters.metadata import AdapterMetadata, adapter
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.protocol import AdapterDriver
from social_media_subscriber.domain.platform import AccountKind, Platform

if TYPE_CHECKING:
    from collections.abc import Iterator


class _DeclaredAdapterDriver(AdapterDriver):
    adapter_metadata: ClassVar[AdapterMetadata]


class _UnreadableEnvironment(Mapping[str, str]):
    @override
    def __getitem__(self, key: str) -> str:
        return pytest.fail(f"unexpected environment read: {key!r}")

    @override
    def __iter__(self) -> Iterator[str]:
        return pytest.fail("unexpected environment iteration")

    @override
    def __len__(self) -> int:
        return pytest.fail("unexpected environment length read")


def test_adapter_operation_contains_only_current_capabilities() -> None:
    # Given / When
    values = tuple(operation.value for operation in AdapterOperation)

    # Then
    assert values == ("collect_account_posts",)


def test_adapter_package_import_does_not_read_environment_or_open_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    def fail_socket_construction(
        family: int = -1,
        socket_type: int = -1,
        protocol: int = -1,
        file_descriptor: int | None = None,
    ) -> socket.socket:
        socket_parameters = family, socket_type, protocol, file_descriptor
        pytest.fail(f"unexpected socket construction: {socket_parameters!r}")

    loaded_adapter_modules = tuple(
        module_name
        for module_name in sys.modules
        if module_name == "social_media_subscriber.adapters"
        or module_name.startswith("social_media_subscriber.adapters.")
    )

    # When
    with monkeypatch.context() as import_guard:
        for module_name in loaded_adapter_modules:
            import_guard.delitem(sys.modules, module_name)
        import_guard.setattr(os, "environ", _UnreadableEnvironment())
        import_guard.setattr(socket, "socket", fail_socket_construction)
        _ = importlib.import_module("social_media_subscriber.adapters")

    # Then
    assert "social_media_subscriber.adapters" in sys.modules


def test_decorator_attaches_one_immutable_metadata_value_to_class_and_instances(
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

    # When
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class SyntheticDriver(_DeclaredAdapterDriver):
        instances_created: ClassVar[int] = 0
        credential_label: str

        def __init__(self, credential_label: str) -> None:
            type(self).instances_created += 1
            self.credential_label = credential_label

    first = SyntheticDriver("first")
    second = SyntheticDriver("second")

    # Then
    assert SyntheticDriver.instances_created == 2
    assert first.adapter_metadata is SyntheticDriver.adapter_metadata
    assert second.adapter_metadata is SyntheticDriver.adapter_metadata
    assert SyntheticDriver.adapter_metadata.platform is Platform.LINKEDIN
    assert SyntheticDriver.adapter_metadata.operations == (
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )
    assert SyntheticDriver.adapter_metadata.account_kinds == (AccountKind.PERSON,)
    assert SyntheticDriver.adapter_metadata.supports_batch is False
    attribute_name = "supports_batch"
    with pytest.raises(FrozenInstanceError):
        setattr(SyntheticDriver.adapter_metadata, attribute_name, True)


def test_decorator_does_not_construct_the_driver() -> None:
    # Given
    instances_created = 0

    # When
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.COMPANY,),
        supports_batch=True,
    )
    class SyntheticBatchDriver(_DeclaredAdapterDriver):
        def __init__(self) -> None:
            nonlocal instances_created
            instances_created += 1

    metadata = SyntheticBatchDriver.adapter_metadata

    # Then
    assert instances_created == 0
    assert metadata.supports_batch is True
