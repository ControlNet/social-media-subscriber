from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from social_media_subscriber.domain import (
    Account,
    AccountId,
    AccountKind,
    Platform,
)
from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    JsonValue,
)
from social_media_subscriber.serialization.json import canonical_json_value_bytes

FIRST_SEEN = datetime(2026, 8, 20, 12, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "brightdata"


def account(*, kind: AccountKind = AccountKind.PERSON) -> Account:
    slug = "synthetic-ada" if kind is AccountKind.PERSON else "synthetic-labs"
    path_kind = "in" if kind is AccountKind.PERSON else "company"
    canonical_url = f"https://www.linkedin.com/{path_kind}/{slug}/"
    return Account(
        id=AccountId(canonical_url),
        platform=Platform.LINKEDIN,
        kind=kind,
        profile_url=canonical_url,
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


def post_with(**updates: str | None) -> BrightDataPost:
    original = post_fixture("synthetic-person-original.json")
    return original.model_copy(update=updates)
