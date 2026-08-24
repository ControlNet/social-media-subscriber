from __future__ import annotations

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain import AccountKind
from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    BrightDataSnapshotEnvelope,
    JsonValue,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalize import normalize_posts
from social_media_subscriber.serialization.json import canonical_json_value_bytes
from tests.unit.test_brightdata_normalizer_support import (
    FIRST_SEEN,
    account,
    post_fixture,
)


def test_provider_models_are_frozen_typed_and_drift_tolerant() -> None:
    post = post_fixture("synthetic-person-original.json")
    envelope = BrightDataSnapshotEnvelope(snapshot_id="synthetic-snapshot-1")

    assert post.id == "urn:li:activity:1001"
    assert post.payload["unknown_nested"] == {"future": [True, None, {"n": 3}]}
    assert envelope.snapshot_id == "synthetic-snapshot-1"
    with pytest.raises(ValidationError):
        post.id = "changed"


def test_original_record_preserves_post_content() -> None:
    record = post_fixture("synthetic-person-original.json")

    post = normalize_posts(account(), (record,), FIRST_SEEN).posts[0]
    expected_content = record.payload.copy()
    for field in (
        "id",
        "date_posted",
        "post_type",
        "url",
        "user_id",
        "use_url",
        "user_url",
        "profile_url",
        "company_url",
    ):
        _ = expected_content.pop(field, None)
    expected_content["text"] = expected_content.pop("post_text")
    expected_content["links"] = expected_content.pop("embedded_links")
    expected_content["images"] = [
        {"url": ("https://media.licdn.com/dms/image/synthetic?signature=redacted")}
    ]
    expected_content["engagement"] = {
        "comments": expected_content.pop("num_comments"),
        "likes": expected_content.pop("num_likes"),
    }

    assert post.content == expected_content
    assert post.content["images"] == expected_content["images"]
    assert post.content["videos"] == record.payload["videos"]
    assert post.content["headline"] == record.payload["headline"]
    assert post.content["title"] == record.payload["title"]
    assert post.content["engagement"] == {"comments": 3, "likes": 42}
    assert post.content["unknown_nested"] == {"future": [True, None, {"n": 3}]}
    assert post.content["links"] == record.payload["embedded_links"]
    assert post.content["hashtags"] == ["Testing", "Synthetic", "Testing"]


def test_collection_metadata_and_related_content_are_not_persisted() -> None:
    record = post_fixture("synthetic-person-original.json")
    additions: dict[str, JsonValue] = {
        "input": {"url": record.url},
        "discovery_input": {
            "url": "https://www.linkedin.com/in/synthetic-ada/",
            "start_date": "2026-08-01T00:00:00Z",
        },
        "timestamp": "2026-08-21T06:48:57Z",
        "more_relevant_posts": [{"id": "unrelated"}],
        "more_articles_by_user": [{"id": "unrelated-article"}],
    }
    payload = record.payload.copy()
    payload.update(additions)
    enriched = BrightDataPost.model_validate_json(canonical_json_value_bytes(payload))

    post = normalize_posts(account(), (enriched,), FIRST_SEEN).posts[0]

    assert (
        not {
            "input",
            "discovery_input",
            "timestamp",
            "more_relevant_posts",
            "more_articles_by_user",
        }
        & post.content.keys()
    )


@pytest.mark.parametrize(
    ("fixture", "post_type"),
    [
        ("synthetic-person-reply.json", "reply"),
        ("synthetic-person-repost.json", "repost"),
        ("synthetic-person-quote.json", "quote"),
        ("synthetic-person-unknown.json", "future_kind"),
    ],
)
def test_all_platform_post_types_are_preserved(fixture: str, post_type: str) -> None:
    result = normalize_posts(account(), (post_fixture(fixture),), FIRST_SEEN)

    assert len(result.posts) == 1
    assert result.posts[0].type == post_type


def test_company_image_only_post_keeps_its_media() -> None:
    result = normalize_posts(
        account(kind=AccountKind.COMPANY),
        (post_fixture("synthetic-company-image-only.json"),),
        FIRST_SEEN,
    )

    assert result.posts[0].content["text"] is None
    assert result.posts[0].content["images"] == [
        {
            "url": (
                "https://media.licdn.com/dms/image/company-synthetic?signature=redacted"
            )
        }
    ]


def test_equivalent_duplicates_collapse_independent_of_order() -> None:
    owner = account()
    record = post_fixture("synthetic-person-original.json")

    forward = normalize_posts(owner, (record, record), FIRST_SEEN)
    reverse = normalize_posts(owner, (record, record), FIRST_SEEN)

    assert forward == reverse
    assert len(forward.posts) == 1


def test_duplicate_observation_drift_collapses_to_first_record() -> None:
    record = post_fixture("synthetic-person-original.json")
    rediscovered = BrightDataPost.model_validate_json(
        canonical_json_value_bytes(record.payload | {"num_likes": 99})
    )

    forward = normalize_posts(account(), (record, rediscovered), FIRST_SEEN)
    reverse = normalize_posts(account(), (rediscovered, record), FIRST_SEEN)

    assert forward == reverse
    assert len(forward.posts) == 1


def test_differing_duplicate_post_content_aborts_deterministically() -> None:
    record = post_fixture("synthetic-person-original.json")
    conflicting = BrightDataPost.model_validate_json(
        canonical_json_value_bytes(record.payload | {"post_text": "Changed"})
    )

    with pytest.raises(BrightDataNormalizationError) as forward:
        _ = normalize_posts(account(), (record, conflicting), FIRST_SEEN)
    with pytest.raises(BrightDataNormalizationError) as reverse:
        _ = normalize_posts(account(), (conflicting, record), FIRST_SEEN)

    assert forward.value.category is BrightDataNormalizationErrorCategory.DUPLICATE
    assert str(forward.value) == str(reverse.value)
    assert "99" not in str(forward.value)


def test_mixed_ownership_aborts_without_exposing_actor_url() -> None:
    record = post_fixture("synthetic-person-original.json").model_copy(
        update={"use_url": "https://www.linkedin.com/in/different-owner/"}
    )

    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = normalize_posts(account(), (record,), FIRST_SEEN)

    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP
    assert "different-owner" not in str(captured.value)
