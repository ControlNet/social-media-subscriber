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
    AdapterIdentityAttempt,
    AdapterIdentityBatch,
    AdapterInstanceOrdinal,
    BatchCompleted,
    CollectedAccount,
    SchemaBatchFailure,
)
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import (
    AccountId,
    PlatformAccountId,
    PlatformPostId,
    account_id_for,
    post_id_for,
)
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent

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


@dataclass(frozen=True, slots=True)
class CompleteBatch:
    posts_by_account: tuple[tuple[Post, ...], ...] = ()


type FakeStep = AdapterAttempt | CompleteBatch


@dataclass(frozen=True, slots=True)
class RouterCall:
    ordinal: AdapterInstanceOrdinal
    account_ids: tuple[AccountId, ...]
    kind: AccountKind


@final
@dataclass(slots=True)
class ScriptedInstance:
    driver_class: type[AdapterDriver]
    ordinal: AdapterInstanceOrdinal
    steps: list[FakeStep]
    calls: list[RouterCall]

    async def collect(self, batch: AdapterBatch) -> AdapterAttempt:
        self.calls.append(
            RouterCall(
                self.ordinal,
                tuple(account.id for account in batch.accounts),
                batch.accounts[0].kind,
            )
        )
        step = self.steps.pop(0) if self.steps else CompleteBatch()
        match step:
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

    async def resolve_identity(
        self,
        batch: AdapterIdentityBatch,
    ) -> AdapterIdentityAttempt:
        """Reject identity use from the collection-only Router fake."""
        _ = batch
        return SchemaBatchFailure()


@final
@dataclass(slots=True)
class ScriptedFactory:
    scripts: tuple[tuple[FakeStep, ...], ...]
    calls: list[RouterCall] = field(default_factory=list)
    created_ordinals: list[AdapterInstanceOrdinal] = field(default_factory=list)

    def create(
        self,
        credential: SecretStr,
        ordinal: AdapterInstanceOrdinal,
    ) -> AdapterInstance:
        _ = credential
        self.created_ordinals.append(ordinal)
        script = self.scripts[ordinal] if ordinal < len(self.scripts) else ()
        return ScriptedInstance(FakeDriver, ordinal, list(script), self.calls)


def make_account(kind: AccountKind, number: int) -> Account:
    platform_id = PlatformAccountId(str(number))
    path = "in" if kind is AccountKind.PERSON else "company"
    return Account(
        id=account_id_for(kind, platform_id),
        platform=Platform.LINKEDIN,
        kind=kind,
        platform_account_id=platform_id,
        profile_url=f"https://www.linkedin.com/{path}/{number}/",
        url_aliases=(),
        first_seen_at=datetime(2026, 8, 20, tzinfo=UTC),
    )


def make_post(account_id: AccountId, number: int) -> Post:
    platform_post_id = PlatformPostId(f"activity-{number}")
    return Post.from_stable(
        StablePostContent(
            schema_version=1,
            id=post_id_for(platform_post_id),
            platform_post_id=platform_post_id,
            account_id=account_id,
            canonical_url=f"https://www.linkedin.com/posts/activity-{number}",
            published_at=datetime(2026, 8, 20, tzinfo=UTC),
            text=f"post {number}",
            kind=PostKind.ORIGINAL,
            hashtags=(),
            links=(),
        ),
        first_seen_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
