from __future__ import annotations

__test__ = False

from datetime import date
from typing import TYPE_CHECKING, Final

from pydantic import SecretStr

from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters import AdapterRegistry
from social_media_subscriber.adapters import instance as instance_contract
from social_media_subscriber.adapters.instance import (
    AdapterPostLocatorRequest,
    AdapterPostRequest,
    CollectedAccount,
    ResolvedLocatorPosts,
)
from social_media_subscriber.adapters.router import Router
from social_media_subscriber.adapters.router_outcomes import (
    RouterDiagnosticCategory,
    RouterRunStatus,
)
from social_media_subscriber.domain.platform import AccountKind
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.normalization_outcomes import (
    SkippedPostCounts,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from tests.fakes.router import FakeDriver, FakeStep, ScriptedFactory

if TYPE_CHECKING:
    from social_media_subscriber.adapters.router_discovery_state import (
        DiscoveryRouterResult,
    )
    from social_media_subscriber.domain.account import Account
    from social_media_subscriber.domain.post import Post

_DISCOVERY_START: Final = date(2026, 8, 14)
_NO_SKIPS: Final = SkippedPostCounts()


def build_router(
    scripts: tuple[tuple[FakeStep, ...], ...],
    keys: tuple[str, ...] = ("test-credential-a", "test-credential-b"),
    locator_scripts: tuple[
        tuple[instance_contract.AdapterPostLocatorAttempt, ...], ...
    ] = (),
) -> tuple[Router, ScriptedFactory]:
    factory = ScriptedFactory(scripts, locator_scripts)
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


def build_locator_request(
    kind: AccountKind,
    number: int,
    *,
    start_date: date = _DISCOVERY_START,
) -> AdapterPostLocatorRequest:
    path = "in" if kind is AccountKind.PERSON else "company"
    locator = parse_linkedin_locator(
        f"https://www.linkedin.com/{path}/router-test-{number}/"
    )
    return AdapterPostLocatorRequest(locator, start_date, date(2026, 8, 21))


def build_resolved_locator(
    request: AdapterPostLocatorRequest,
    account: Account,
    *,
    posts: tuple[Post, ...] = (),
    sources: tuple[BrightDataLinkedInPostSourceRecord, ...] = (),
    skipped: SkippedPostCounts = _NO_SKIPS,
) -> ResolvedLocatorPosts:
    return ResolvedLocatorPosts(
        request.locator,
        account,
        CollectedAccount(account.id, posts, sources, skipped),
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
        user_id=account.platform_account_id,
        post_text=text,
    )
    return BrightDataLinkedInPostSourceRecord.from_post(account.id, post)


def assert_locator_schema_abort(result: DiscoveryRouterResult) -> None:
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.accounts == ()
    assert result.outcomes == ()
    assert result.posts == ()
    assert result.source_records == ()
    assert result.skipped == SkippedPostCounts()
    assert tuple(item.category for item in result.diagnostics) == (
        RouterDiagnosticCategory.SCHEMA_ABORT,
    )
