from __future__ import annotations

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain import AccountKind
from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    BrightDataSnapshotEnvelope,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from social_media_subscriber.providers.brightdata.normalize import normalize_posts
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
    source_record_path,
)
from social_media_subscriber.serialization.json import (
    canonical_json_bytes,
    canonical_json_value_bytes,
)
from tests.unit.test_brightdata_normalizer_support import (
    FIRST_SEEN,
    account,
    post_fixture,
)


def test_provider_models_are_frozen_typed_and_drift_tolerant() -> None:
    # Given / When
    post = post_fixture("synthetic-person-original.json")
    envelope = BrightDataSnapshotEnvelope(snapshot_id="synthetic-snapshot-1")

    # Then
    assert post.id == "urn:li:activity:1001"
    assert post.payload["unknown_nested"] == {"future": [True, None, {"n": 3}]}
    assert envelope.snapshot_id == "synthetic-snapshot-1"
    with pytest.raises(ValidationError):
        post.id = "changed"


def test_original_records_preserve_complete_source_and_allowlist_canonical_fields() -> (
    None
):
    # Given
    record = post_fixture("synthetic-person-original.json")

    # When
    result = normalize_posts(account(), (record,), FIRST_SEEN)
    source = result.source_records[0]
    canonical = result.posts[0]

    # Then
    assert result.skipped.total == 0
    assert source.payload == record.payload
    assert source.payload["images"] == [
        "https://media.licdn.com/dms/image/synthetic?signature=redacted"
    ]
    assert source.payload["num_likes"] == 42
    assert source.payload["headline"] == "Explicitly synthetic headline"
    assert "num_likes" not in canonical.model_fields_set
    assert not hasattr(canonical, "images")
    assert canonical.links == (
        "https://example.test/article?a=1",
        "https://example.test/default",
        "https://example.test/plain",
    )
    assert canonical.hashtags == ("Synthetic", "Testing")


@pytest.mark.parametrize(
    ("fixture", "skip_field"),
    [
        ("synthetic-person-reply.json", "replies"),
        ("synthetic-person-repost.json", "reposts"),
        ("synthetic-person-quote.json", "quotes"),
        ("synthetic-person-unknown.json", "unknown"),
    ],
)
def test_non_original_kinds_are_skipped_without_persisting_source(
    fixture: str,
    skip_field: str,
) -> None:
    # Given
    record = post_fixture(fixture)

    # When
    result = normalize_posts(account(), (record,), FIRST_SEEN)

    # Then
    assert result.source_records == ()
    assert result.posts == ()
    assert getattr(result.skipped, skip_field) == 1
    assert result.skipped.total == 1


def test_company_image_only_original_is_canonical_without_media() -> None:
    # Given / When
    result = normalize_posts(
        account(kind=AccountKind.COMPANY),
        (post_fixture("synthetic-company-image-only.json"),),
        FIRST_SEEN,
    )

    # Then
    assert result.posts[0].text is None
    assert result.source_records[0].payload["images"] == [
        "https://media.licdn.com/dms/image/company-synthetic?signature=redacted"
    ]


def test_source_identity_path_and_payload_hash_are_independent() -> None:
    # Given
    owner = account()
    original = post_fixture("synthetic-person-original.json")
    changed = BrightDataPost.model_validate_json(
        canonical_json_value_bytes(original.payload | {"num_likes": 43}),
    )

    # When
    first = BrightDataLinkedInPostSourceRecord.from_post(owner.id, original)
    second = BrightDataLinkedInPostSourceRecord.from_post(owner.id, changed)

    # Then
    assert source_record_path(first) == source_record_path(second)
    assert source_record_path(first).as_posix() == (
        "source/brightdata/linkedin/posts/"
        "8f8c5bf42265219bb0bc37c0da49e3cca6c2a7781899912a28ba036be3add5e2.json"
    )
    assert first.payload_sha256 != second.payload_sha256
    assert canonical_json_bytes(first) != canonical_json_bytes(second)


def test_equivalent_duplicates_collapse_independent_of_order() -> None:
    # Given
    owner = account()
    record = post_fixture("synthetic-person-original.json")

    # When
    forward = normalize_posts(owner, (record, record), FIRST_SEEN)
    reverse = normalize_posts(owner, (record, record), FIRST_SEEN)

    # Then
    assert forward == reverse
    assert len(forward.source_records) == 1
    assert len(forward.posts) == 1


def test_differing_duplicate_payload_aborts_deterministically() -> None:
    # Given
    record = post_fixture("synthetic-person-original.json")
    conflicting = BrightDataPost.model_validate_json(
        canonical_json_value_bytes(record.payload | {"num_likes": 99})
    )

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as forward:
        _ = normalize_posts(account(), (record, conflicting), FIRST_SEEN)
    with pytest.raises(BrightDataNormalizationError) as reverse:
        _ = normalize_posts(account(), (conflicting, record), FIRST_SEEN)
    assert forward.value.category is BrightDataNormalizationErrorCategory.DUPLICATE
    assert str(forward.value) == str(reverse.value)
    assert "99" not in str(forward.value)


def test_mixed_ownership_aborts_without_exposing_actor_url() -> None:
    # Given
    record = post_fixture("synthetic-person-original.json").model_copy(
        update={"use_url": "https://www.linkedin.com/in/different-owner/"}
    )

    # When / Then
    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = normalize_posts(account(), (record,), FIRST_SEEN)
    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP
    assert "different-owner" not in str(captured.value)
