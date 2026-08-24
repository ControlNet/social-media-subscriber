from __future__ import annotations

__test__ = False

from datetime import date
from typing import TYPE_CHECKING

from pydantic import SecretStr

from social_media_subscriber.adapters import AdapterRegistry
from social_media_subscriber.adapters.instance import (
    AdapterInstanceSpec,
    AdapterPostRequest,
)
from social_media_subscriber.adapters.router import Router
from tests.fakes.router import FakeDriver, FakeStep, ScriptedFactory

if TYPE_CHECKING:
    from social_media_subscriber.domain.account import Account


def build_router(
    scripts: tuple[tuple[FakeStep, ...], ...],
    keys: tuple[str, ...] = ("test-credential-a", "test-credential-b"),
) -> tuple[Router, ScriptedFactory]:
    factory = ScriptedFactory(scripts)
    router = Router(
        AdapterRegistry((FakeDriver,)),
        tuple(AdapterInstanceSpec(FakeDriver, factory, SecretStr(key)) for key in keys),
    )
    return router, factory


def build_post_requests(
    accounts: tuple[Account, ...],
) -> tuple[AdapterPostRequest, ...]:
    return tuple(
        AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20))
        for account in accounts
    )
