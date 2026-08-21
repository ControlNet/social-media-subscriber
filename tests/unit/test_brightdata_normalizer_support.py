from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from social_media_subscriber.accounts.identity import AccountIdentityService
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.domain import (
    Account,
    AccountKind,
    Platform,
    PlatformAccountId,
)
from social_media_subscriber.domain.ids import account_id_for
from social_media_subscriber.providers.brightdata.discovery import (
    PostsAccountDiscoveryOutcome,
    derive_account_from_posts,
)
from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    JsonValue,
)
from social_media_subscriber.serialization.json import canonical_json_value_bytes

FIRST_SEEN = datetime(2026, 8, 20, 12, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "brightdata"


def account(*, kind: AccountKind = AccountKind.PERSON) -> Account:
    platform_id = PlatformAccountId("12345" if kind is AccountKind.PERSON else "67890")
    slug = "synthetic-ada" if kind is AccountKind.PERSON else "synthetic-labs"
    path_kind = "in" if kind is AccountKind.PERSON else "company"
    return Account(
        id=account_id_for(kind, platform_id),
        platform=Platform.LINKEDIN,
        kind=kind,
        platform_account_id=platform_id,
        profile_url=f"https://www.linkedin.com/{path_kind}/{slug}/",
        url_aliases=(),
        first_seen_at=FIRST_SEEN,
    )


def post_fixture(name: str) -> BrightDataPost:
    return BrightDataPost.model_validate_json((FIXTURES / name).read_bytes())


def post_with_links(*links: str) -> BrightDataPost:
    original = post_fixture("synthetic-person-original.json")
    embedded_links: list[JsonValue] = list(links)
    payload = original.payload.copy()
    payload["embedded_links"] = embedded_links
    return BrightDataPost.model_validate_json(canonical_json_value_bytes(payload))


def discovery_post(**updates: str | None) -> BrightDataPost:
    original = post_fixture("synthetic-person-original.json")
    return original.model_copy(update=updates)


def derive(
    records: tuple[BrightDataPost, ...],
    *,
    known_accounts: tuple[Account, ...] = (),
) -> PostsAccountDiscoveryOutcome:
    return derive_account_from_posts(
        requested_locator=parse_linkedin_locator(
            "https://www.linkedin.com/in/synthetic-ada/"
        ),
        records=records,
        identity_service=AccountIdentityService(known_accounts),
        first_seen_at=FIRST_SEEN,
    )
