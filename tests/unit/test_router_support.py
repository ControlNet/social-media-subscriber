from __future__ import annotations

__test__ = False

from datetime import date
from typing import TYPE_CHECKING

from pydantic import SecretStr

from social_media_subscriber.adapters import AdapterRegistry
from social_media_subscriber.adapters.instance import AdapterPostRequest
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
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
        factory,
        tuple(SecretStr(key) for key in keys),
    )
    return router, factory


def build_post_requests(
    accounts: tuple[Account, ...],
) -> tuple[AdapterPostRequest, ...]:
    return tuple(
        AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20))
        for account in accounts
    )


def build_source_record(
    account: Account,
    platform_post_id: str,
    text: str,
) -> BrightDataLinkedInPostSourceRecord:
    post = BrightDataPost(
        id=platform_post_id,
        date_posted="2026-08-20T12:00:00+00:00",
        post_type="post",
        url=f"https://www.linkedin.com/posts/{platform_post_id}",
        user_url=account.profile_url,
        post_text=text,
    )
    return BrightDataLinkedInPostSourceRecord.from_post(account.id, post)
