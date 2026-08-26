from datetime import UTC, datetime

import pytest

from social_media_subscriber.platforms.linkedin import (
    LinkedInPostUrlError,
    canonical_platform_post_id,
    canonical_post_timestamp,
    canonical_post_type,
    canonical_post_url,
)


def test_activity_urn_matches_numeric_provider_id() -> None:
    assert canonical_platform_post_id("urn:li:activity:1001") == "1001"
    assert canonical_platform_post_id("1001") == "1001"


def test_other_urn_namespaces_remain_distinct() -> None:
    assert canonical_platform_post_id("urn:li:share:1001") == ("urn:li:share:1001")
    assert canonical_platform_post_id("urn:li:ugcPost:1001") == ("urn:li:ugcPost:1001")


def test_numeric_activity_ids_produce_one_provider_independent_post_url() -> None:
    expected = "https://www.linkedin.com/feed/update/urn:li:activity:1001/"

    assert (
        canonical_post_url(
            "https://linkedin.com/posts/example_first-route-1001/",
            platform_post_id="1001",
        )
        == expected
    )
    assert (
        canonical_post_url(
            "https://www.linkedin.com/posts/example_second-route-1001/?trk=source",
            platform_post_id="urn:li:activity:1001",
        )
        == expected
    )


def test_non_activity_ids_keep_the_validated_provider_post_url() -> None:
    assert (
        canonical_post_url(
            "https://linkedin.com/posts/example_share-1001/?trk=source",
            platform_post_id="urn:li:share:1001",
        )
        == "https://www.linkedin.com/posts/example_share-1001/"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/posts/../synthetic",
        "https://www.linkedin.com/feed/update/./synthetic",
    ],
)
def test_post_url_rejects_literal_dot_segments(url: str) -> None:
    # Given / When / Then
    with pytest.raises(LinkedInPostUrlError):
        _ = canonical_post_url(url)


def test_platform_timestamp_discards_provider_precision_below_one_second() -> None:
    assert canonical_post_timestamp(
        datetime(2026, 8, 19, 9, 0, 0, 35_000, tzinfo=UTC)
    ) == datetime(2026, 8, 19, 9, tzinfo=UTC)


def test_repost_marker_only_overrides_a_generic_post_type() -> None:
    assert canonical_post_type("POST", is_repost=True) == "repost"
    assert canonical_post_type("quote", is_repost=True) == "quote"
    assert canonical_post_type("reply", is_repost=False) == "reply"
