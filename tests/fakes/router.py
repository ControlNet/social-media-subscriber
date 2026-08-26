from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, final

from social_media_subscriber.adapters import (
    AdapterMetadata,
    AdapterOperation,
    adapter,
)
from social_media_subscriber.adapters.instance import (
    AdapterAttempt,
    AdapterBatch,
    AdapterInstanceOrdinal,
    BatchCompleted,
    CollectedAccount,
)
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import AccountId, PlatformPostId
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.post import Post

if TYPE_CHECKING:
    from pydantic import SecretStr

    from social_media_subscriber.adapters.instance import AdapterInstance
    from social_media_subscriber.adapters.protocol import AdapterDriver


class DeclaredFakeDriver:
    adapter_metadata: ClassVar[AdapterMetadata]


@adapter(
    platform=Platform.LINKEDIN,
    operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
    account_kinds=(AccountKind.PERSON, AccountKind.COMPANY),
    supports_batch=True,
)
class FakeDriver(DeclaredFakeDriver):
    pass


@adapter(
    platform=Platform.LINKEDIN,
    operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
    account_kinds=(AccountKind.PERSON, AccountKind.COMPANY),
    supports_batch=True,
)
class FallbackFakeDriver(DeclaredFakeDriver):
    pass


@adapter(
    platform=Platform.LINKEDIN,
    operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
    account_kinds=(AccountKind.PERSON, AccountKind.COMPANY),
    supports_batch=False,
)
class NonBatchFakeDriver(DeclaredFakeDriver):
    pass


@adapter(
    platform=Platform.X,
    operations=(AdapterOperation.COLLECT_ACCOUNT_POSTS,),
    account_kinds=(AccountKind.PROFILE,),
    supports_batch=True,
)
class XFakeDriver(DeclaredFakeDriver):
    pass


@dataclass(frozen=True, slots=True)
class CompleteBatch:
    posts_by_account: tuple[tuple[Post, ...], ...] = ()


type FakeStep = AdapterAttempt | CompleteBatch


@dataclass(frozen=True, slots=True)
class RouterCall:
    ordinal: AdapterInstanceOrdinal
    account_ids: tuple[AccountId, ...]
    platform: Platform
    kind: AccountKind


@final
@dataclass(slots=True)  # Scripted fixture records calls and steps.
class ScriptedInstance:
    driver_class: type[AdapterDriver]
    ordinal: AdapterInstanceOrdinal
    steps: list[FakeStep]
    calls: list[RouterCall]
    close_calls: int = 0

    async def aclose(self) -> None:
        self.close_calls += 1

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        self.calls.append(
            RouterCall(
                self.ordinal,
                tuple(account.id for account in batch.accounts),
                batch.accounts[0].platform,
                batch.accounts[0].kind,
            )
        )
        step = self.steps.pop(0) if self.steps else CompleteBatch()
        match step:  # Fallback returns typed AdapterAttempt variants.
            case CompleteBatch(posts_by_account=posts_by_account):
                return BatchCompleted(
                    tuple(
                        CollectedAccount(
                            account.id,
                            posts_by_account[index]
                            if index < len(posts_by_account)
                            else (),
                        )
                        for index, account in enumerate(batch.accounts)
                    )
                )
            case _:
                return step


@final
@dataclass(slots=True)  # Factory records created test instances.
class ScriptedFactory:
    scripts: tuple[tuple[FakeStep, ...], ...]
    driver_class: type[AdapterDriver] = FakeDriver
    calls: list[RouterCall] = field(default_factory=list)
    created_ordinals: list[AdapterInstanceOrdinal] = field(default_factory=list)
    instances: list[ScriptedInstance] = field(default_factory=list)

    def create(
        self,
        credential: SecretStr,
        ordinal: AdapterInstanceOrdinal,
    ) -> AdapterInstance:
        _ = credential
        script_index = len(self.created_ordinals)
        self.created_ordinals.append(ordinal)
        script = self.scripts[script_index] if script_index < len(self.scripts) else ()
        instance = ScriptedInstance(
            self.driver_class,
            ordinal,
            list(script),
            self.calls,
        )
        self.instances.append(instance)
        return instance


def make_account(kind: AccountKind, number: int) -> Account:
    path = "in" if kind is AccountKind.PERSON else "company"
    profile_url = f"https://www.linkedin.com/{path}/synthetic-{number}/"
    return Account(
        platform=Platform.LINKEDIN,
        kind=kind,
        profile_url=profile_url,
        first_seen_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def make_post(account_id: AccountId, number: int) -> Post:
    platform_post_id = PlatformPostId(f"activity-{number}")
    return Post(
        platform_post_id=platform_post_id,
        account_profile_url=account_id,
        canonical_url=f"https://www.linkedin.com/posts/activity-{number}",
        published_at=datetime(2026, 8, 20, tzinfo=UTC),
        type="post",
        content={"text": f"post {number}", "hashtags": [], "links": []},
        first_seen_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def make_x_account(number: int) -> Account:
    return Account(
        platform=Platform.X,
        kind=AccountKind.PROFILE,
        profile_url=f"https://x.com/synthetic_{number}/",
        first_seen_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def make_x_post(account_id: AccountId, number: int) -> Post:
    platform_post_id = PlatformPostId(str(number))
    return Post(
        platform_post_id=platform_post_id,
        account_profile_url=account_id,
        canonical_url=f"{account_id}status/{number}",
        published_at=datetime(2026, 8, 20, tzinfo=UTC),
        type="post",
        content={"text": f"X post {number}"},
        first_seen_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
