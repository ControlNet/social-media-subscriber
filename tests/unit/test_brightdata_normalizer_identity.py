from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.providers.brightdata import discovery
from social_media_subscriber.providers.brightdata.discovery import (
    ResolvedPostsAccountDiscovery,
    UnresolvedPostsAccountDiscovery,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from tests.unit.test_brightdata_normalizer_support import (
    account,
    derive,
    discovery_post,
    post_fixture,
)

if TYPE_CHECKING:
    from social_media_subscriber.domain import Account
    from social_media_subscriber.providers.brightdata.models import BrightDataPost
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        BrightDataNormalizationResult,
    )


def test_derive_numeric_identity_normalizes_complete_tuple() -> None:
    # Given
    ordinary = discovery_post(user_id="00123")
    reply = post_fixture("synthetic-person-reply.json").model_copy(
        update={"user_id": "00123", "use_url": ordinary.use_url}
    )

    # When
    result = derive((ordinary, reply))

    # Then
    assert isinstance(result, ResolvedPostsAccountDiscovery)
    assert result.account.platform_account_id == "00123"
    assert result.account.id == "linkedin:person:00123"
    assert len(result.normalization.source_records) == 2
    assert len(result.normalization.posts) == 1
    assert result.normalization.skipped.replies == 1


@pytest.mark.parametrize(
    "user_id",
    [
        None,
        "",
        " 123",
        "123 ",
        "+123",
        "123.0",
        "\N{FULLWIDTH DIGIT ONE}\N{FULLWIDTH DIGIT TWO}\N{FULLWIDTH DIGIT THREE}",
    ],
)
def test_derive_numeric_identity_rejects_missing_or_nonnumeric_ids(
    user_id: str | None,
) -> None:
    # Given
    ordinary = discovery_post(user_id=user_id)

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = derive((ordinary,))
    assert captured.value.category is BrightDataNormalizationErrorCategory.IDENTITY
    assert user_id is None or not user_id or user_id not in str(captured.value)


def test_derive_numeric_identity_rejects_mixed_ids() -> None:
    # Given
    first = discovery_post(user_id="00123")
    second = discovery_post(id="urn:li:activity:1002", user_id="00456")

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = derive((first, second))
    assert captured.value.category is BrightDataNormalizationErrorCategory.IDENTITY
    assert "00123" not in str(captured.value)
    assert "00456" not in str(captured.value)


@pytest.mark.parametrize(
    "post_type",
    ["repost", "reply", "quote", "unknown"],
)
def test_derive_numeric_identity_returns_unresolved_for_nonoriginal_only(
    post_type: str,
) -> None:
    # Given
    record = discovery_post(post_type=post_type, user_id="00123")

    # When
    result = derive((record,))

    # Then
    assert isinstance(result, UnresolvedPostsAccountDiscovery)


def test_derive_numeric_identity_returns_unresolved_for_zero_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    def unexpected_normalize(
        _account: Account,
        _records: tuple[BrightDataPost, ...],
        _first_seen_at: datetime,
    ) -> BrightDataNormalizationResult:
        raise AssertionError

    monkeypatch.setattr(discovery, "normalize_posts", unexpected_normalize)

    # When
    result = derive(())

    # Then
    assert isinstance(result, UnresolvedPostsAccountDiscovery)


def test_derive_numeric_identity_reconciles_changed_slug_by_stable_id() -> None:
    # Given
    known = account().model_copy(
        update={
            "profile_url": "https://www.linkedin.com/in/synthetic-prior/",
            "url_aliases": (),
        }
    )

    # When
    result = derive((discovery_post(user_id="12345"),), known_accounts=(known,))

    # Then
    assert isinstance(result, ResolvedPostsAccountDiscovery)
    assert result.account.profile_url == known.profile_url
    assert result.account.first_seen_at == known.first_seen_at
    assert "https://www.linkedin.com/in/synthetic-ada/" in result.account.url_aliases
    assert "https://www.linkedin.com/in/synthetic-prior/" in result.account.url_aliases


def test_derive_same_numeric_identity_is_order_independent_for_known_aliases() -> None:
    # Given
    known = account().model_copy(
        update={
            "profile_url": "https://www.linkedin.com/in/synthetic-prior/",
            "url_aliases": ("https://www.linkedin.com/in/synthetic-earliest/",),
            "first_seen_at": datetime(2025, 1, 1, tzinfo=UTC),
        }
    )
    records = (
        discovery_post(user_id="12345"),
        discovery_post(
            id="urn:li:activity:1002",
            user_id="12345",
            use_url=known.url_aliases[0],
        ),
    )

    # When
    forward = derive(records, known_accounts=(known,))
    reverse = derive(records[::-1], known_accounts=(known,))

    # Then
    assert forward == reverse
    assert isinstance(forward, ResolvedPostsAccountDiscovery)
    assert forward.account.profile_url == known.profile_url
    assert forward.account.first_seen_at == known.first_seen_at
    assert forward.account.url_aliases == (
        "https://www.linkedin.com/in/synthetic-ada/",
        "https://www.linkedin.com/in/synthetic-earliest/",
        "https://www.linkedin.com/in/synthetic-prior/",
    )
