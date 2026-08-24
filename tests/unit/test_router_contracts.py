from __future__ import annotations

__test__ = False

from social_media_subscriber.adapters import (
    AdapterOperation,
    AdapterRegistry,
    adapter,
)
from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.providers.brightdata.client import BrightDataClient
from tests.fakes.router import DeclaredFakeDriver, FakeDriver


def test_adapter_surface_exposes_only_normal_account_posts() -> None:
    assert tuple(AdapterOperation) == (AdapterOperation.COLLECT_ACCOUNT_POSTS,)
    assert not hasattr(Router, "resolve_identities")
    assert not hasattr(Router, "discover_posts")
    assert not hasattr(instance_contract.AdapterInstance, "resolve_identity")
    assert not hasattr(instance_contract.AdapterInstance, "discover_posts")
    assert not hasattr(BrightDataClient, "resolve_person_identities")
    assert not hasattr(BrightDataClient, "resolve_company_identities")


def test_registry_resolution_preserves_declared_candidate_order() -> None:
    @adapter(
        platform=Platform.LINKEDIN,
        operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
        account_kinds=(AccountKind.PERSON,),
        supports_batch=False,
    )
    class SecondFakeDriver(DeclaredFakeDriver):
        pass

    registry = AdapterRegistry((SecondFakeDriver, FakeDriver))

    result = registry.resolve(
        platform=Platform.LINKEDIN,
        operation=AdapterOperation.COLLECT_ACCOUNT_POSTS,
        account_kind=AccountKind.PERSON,
    )

    assert result == (SecondFakeDriver, FakeDriver)
