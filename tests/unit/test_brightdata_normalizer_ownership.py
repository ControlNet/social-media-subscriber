from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.domain import AccountId, PlatformAccountId
from social_media_subscriber.providers.brightdata import discovery
from social_media_subscriber.providers.brightdata.discovery import (
    ResolvedPostsAccountDiscovery,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalize import normalize_posts
from tests.unit.test_brightdata_normalizer_support import (
    account,
    derive,
    discovery_post,
)

if TYPE_CHECKING:
    from social_media_subscriber.domain import Account
    from social_media_subscriber.providers.brightdata.models import BrightDataPost
    from social_media_subscriber.providers.brightdata.normalization_outcomes import (
        BrightDataNormalizationResult,
    )


@pytest.mark.parametrize(
    "actor_field", ["use_url", "user_url", "profile_url", "company_url"]
)
def test_derive_numeric_identity_rejects_malformed_actor_on_nonoriginal_records(
    actor_field: str,
) -> None:
    # Given
    record = discovery_post(
        post_type="reply",
        **{actor_field: "not-a-linkedin-locator"},
    )

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = derive((record,))
    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP


@pytest.mark.parametrize(
    "records",
    [
        (
            discovery_post(user_id="12345"),
            discovery_post(id="urn:li:activity:1002", user_id="99999"),
        ),
        (
            discovery_post(user_id="12345"),
            discovery_post(
                id="urn:li:activity:1002",
                user_id="12345",
                use_url="https://www.linkedin.com/in/synthetic-conflict/",
            ),
        ),
    ],
    ids=("mixed-id", "actor-conflict"),
)
def test_derive_adversarial_failure_category_is_record_order_independent(
    records: tuple[BrightDataPost, ...],
) -> None:
    # Given / When
    captured: list[BrightDataNormalizationError] = []
    for ordered in (records, records[::-1]):
        with pytest.raises(BrightDataNormalizationError) as error:
            _ = derive(ordered)
        captured.append(error.value)

    # Then
    assert captured[0].category is captured[1].category
    assert str(captured[0]) == str(captured[1])
    assert "synthetic-conflict" not in repr(captured)


@pytest.mark.parametrize(
    "actor_field", ["use_url", "user_url", "profile_url", "company_url"]
)
@pytest.mark.parametrize(
    "actor_url",
    [
        "not-a-linkedin-locator",
        "https://www.linkedin.com/company/synthetic-labs/",
        "https://www.linkedin.com/in/synthetic-other/",
    ],
)
def test_derive_numeric_identity_rejects_each_malformed_actor_conflict(
    actor_field: str,
    actor_url: str,
) -> None:
    # Given
    ordinary = discovery_post(**{actor_field: actor_url})

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = derive((ordinary,))
    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP
    assert "synthetic-other" not in str(captured.value)
    assert "synthetic-labs" not in str(captured.value)


@pytest.mark.parametrize(
    "actor_field", ["use_url", "user_url", "profile_url", "company_url"]
)
def test_derive_numeric_identity_rejects_known_alias_actor_conflict(
    actor_field: str,
) -> None:
    # Given
    owner = account().model_copy(
        update={"url_aliases": ("https://www.linkedin.com/in/synthetic-known/",)}
    )
    conflicting = account().model_copy(
        update={
            "platform_account_id": PlatformAccountId("99999"),
            "id": AccountId("linkedin:person:99999"),
            "profile_url": "https://www.linkedin.com/in/synthetic-other/",
        }
    )
    ordinary = discovery_post(
        user_id="12345",
        **{actor_field: "https://www.linkedin.com/in/synthetic-other/"},
    )

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = derive((ordinary,), known_accounts=(owner, conflicting))
    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP
    assert "synthetic-other" not in str(captured.value)


def test_derive_numeric_identity_rejects_candidate_conflicting_with_known_locator() -> (
    None
):
    # Given
    ordinary = discovery_post(user_id="99999")

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = derive((ordinary,), known_accounts=(account(),))
    assert captured.value.category is BrightDataNormalizationErrorCategory.IDENTITY
    assert "99999" not in str(captured.value)


def test_derive_numeric_identity_redacts_actor_canary() -> None:
    # Given
    actor_canary = "EXPLICIT_TEST_ONLY_ACTOR_CANARY"
    ordinary = discovery_post(use_url=f"https://invalid.example/{actor_canary}")

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = derive((ordinary,))
    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP
    assert actor_canary not in str(captured.value)


def test_derive_numeric_identity_calls_normalize_once_after_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    known = account().model_copy(
        update={"first_seen_at": datetime(2025, 1, 1, tzinfo=UTC)}
    )
    calls: list[tuple[Account, tuple[BrightDataPost, ...]]] = []
    actual_normalize = normalize_posts

    def capture_normalize(
        owner: Account,
        records: tuple[BrightDataPost, ...],
        first_seen_at: datetime,
    ) -> BrightDataNormalizationResult:
        calls.append((owner, records))
        return actual_normalize(owner, records, first_seen_at)

    monkeypatch.setattr(discovery, "normalize_posts", capture_normalize)
    records = (discovery_post(user_id="12345"),)

    # When
    result = derive(records, known_accounts=(known,))

    # Then
    assert isinstance(result, ResolvedPostsAccountDiscovery)
    assert calls == [(result.account, records)]
    assert calls[0][0].first_seen_at == known.first_seen_at
