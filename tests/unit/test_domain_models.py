from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from multiprocessing import get_context

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import PostId, record_filename
from social_media_subscriber.domain.post import Post, StablePostContent
from social_media_subscriber.domain.post_merge import (
    PostMergeConflictError,
    merge_post,
)
from social_media_subscriber.serialization.json import canonical_json_bytes
from tests.unit import test_domain_account as account
from tests.unit import test_domain_post as post
from tests.unit.test_domain_account import FIRST_SEEN, _account
from tests.unit.test_domain_post import _post, _stable_post


def _import_domain_package() -> None:
    __import__("social_media_subscriber.domain")


def test_domain_package_imports_in_a_fresh_spawned_interpreter() -> None:
    # Given
    process = get_context("spawn").Process(target=_import_domain_package)

    # When
    process.start()
    process.join(timeout=10)

    # Then
    assert process.exitcode == 0


def test_models_and_internal_stable_content_are_frozen() -> None:
    # Given
    account_model = _account()
    stable = _stable_post(account_model.id)

    # When / Then
    with pytest.raises(ValidationError):
        Account.__setattr__(account_model, "profile_url", "https://example.test/")
    with pytest.raises(FrozenInstanceError):
        StablePostContent.__setattr__(stable, "text", "changed")


def test_post_serialization_is_deterministic_for_shuffled_collections() -> None:
    # Given
    account_model = _account()
    stable = _stable_post(account_model.id)
    shuffled = replace(
        stable,
        hashtags=tuple(reversed(stable.hashtags)),
        links=tuple(reversed(stable.links)),
    )

    # When
    first = Post.from_stable(stable, FIRST_SEEN)
    second = Post.from_stable(shuffled, FIRST_SEEN)

    # Then
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_content_hash_excludes_first_seen_time() -> None:
    # Given
    account_model = _account()
    first = _post(account_model.id)

    # When
    rediscovered = first.model_copy(
        update={"first_seen_at": FIRST_SEEN + timedelta(days=5)}
    )

    # Then
    assert rediscovered.content_hash == first.content_hash


def test_repeated_unchanged_merge_preserves_first_seen_and_bytes() -> None:
    # Given
    account_model = _account()
    existing = _post(account_model.id)
    rediscovered = existing.model_copy(
        update={"first_seen_at": FIRST_SEEN + timedelta(days=5)}
    )

    # When
    merged = merge_post(existing, rediscovered)

    # Then
    assert merged is existing
    assert merged.first_seen_at is FIRST_SEEN
    assert canonical_json_bytes(merged) == canonical_json_bytes(existing)


def test_merge_rejects_conflicting_content_for_one_post_id() -> None:
    # Given
    account_model = _account()
    existing = _post(account_model.id)
    conflicting = Post.from_stable(
        replace(_stable_post(account_model.id), text="different"),
        FIRST_SEEN + timedelta(days=5),
    )

    # When / Then
    with pytest.raises(PostMergeConflictError) as captured:
        _ = merge_post(existing, conflicting)
    assert captured.value.post_id == existing.id


def test_record_filename_is_safe_for_malicious_external_ids() -> None:
    # Given
    malicious = PostId("linkedin:post:../../?token=canary\n")

    # When
    filename = record_filename(malicious)

    # Then
    assert len(filename) == 69
    assert filename.endswith(".json")
    assert filename[:-5].isalnum()
    assert filename[:-5] == filename[:-5].lower()


test_account_canonical_url_identity_is_the_profile_url = (
    account.test_account_canonical_url_identity_is_the_profile_url
)
test_account_round_trip_preserves_schema_v2_url_identity = (
    account.test_account_round_trip_preserves_schema_v2_url_identity
)
test_account_rejects_noncanonical_url_identity = (
    account.test_account_rejects_noncanonical_url_identity
)
test_account_rejects_invalid_boundary_values = (
    account.test_account_rejects_invalid_boundary_values
)
test_account_rejects_wrong_kind_url_identity = (
    account.test_account_rejects_wrong_kind_url_identity
)
test_account_rejects_mismatched_canonical_profile_url = (
    account.test_account_rejects_mismatched_canonical_profile_url
)
test_account_rejects_legacy_numeric_or_alias_identity = (
    account.test_account_rejects_legacy_numeric_or_alias_identity
)
test_account_boundary_error_representations_redact_invalid_account_id = (
    account.test_account_boundary_error_representations_redact_invalid_account_id
)
test_post_normalizes_stable_content_and_verifies_hash = (
    post.test_post_normalizes_stable_content_and_verifies_hash
)
test_post_rejects_invalid_boundary_values = (
    post.test_post_rejects_invalid_boundary_values
)
test_post_rejects_content_hash_that_does_not_match_stable_fields = (
    post.test_post_rejects_content_hash_that_does_not_match_stable_fields
)
test_stable_post_content_rejects_control_characters_in_canonical_url = (
    post.test_stable_post_content_rejects_control_characters_in_canonical_url
)
test_stable_post_content_rejects_control_characters_in_approved_link = (
    post.test_stable_post_content_rejects_control_characters_in_approved_link
)
test_stable_post_content_rejects_encoded_structural_canonical_url = (
    post.test_stable_post_content_rejects_encoded_structural_canonical_url
)
test_stable_post_content_rejects_encoded_structural_approved_link = (
    post.test_stable_post_content_rejects_encoded_structural_approved_link
)
test_post_accepts_canonical_url_account_id = (
    post.test_post_accepts_canonical_url_account_id
)
test_post_from_stable_rejects_malformed_account_id_before_hash = (
    post.test_post_from_stable_rejects_malformed_account_id_before_hash
)
test_post_boundary_rejects_malformed_account_id_with_stable_field_error = (
    post.test_post_boundary_rejects_malformed_account_id_with_stable_field_error
)
test_post_boundary_error_representations_redact_invalid_account_id = (
    post.test_post_boundary_error_representations_redact_invalid_account_id
)
