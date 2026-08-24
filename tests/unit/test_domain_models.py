from __future__ import annotations

from datetime import timedelta
from multiprocessing import get_context

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import PostId, record_filename
from social_media_subscriber.domain.post import Post
from social_media_subscriber.domain.post_merge import (
    PostMergeConflictError,
    merge_post,
)
from social_media_subscriber.serialization.json import canonical_json_bytes
from tests.unit.test_domain_account import FIRST_SEEN, _account
from tests.unit.test_domain_post import _post


def _import_domain_package() -> None:
    __import__("social_media_subscriber.domain")


def test_domain_package_imports_in_a_fresh_spawned_interpreter() -> None:
    process = get_context("spawn").Process(target=_import_domain_package)
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 0


def test_domain_models_are_frozen() -> None:
    account_model = _account()
    post = _post(account_model.id)

    with pytest.raises(ValidationError):
        Account.__setattr__(account_model, "profile_url", "https://example.test/")
    with pytest.raises(ValidationError):
        Post.__setattr__(post, "type", "changed")


def test_post_serialization_is_deterministic_for_shuffled_content_keys() -> None:
    first = _post(_account().id)
    second = first.model_copy(
        update={"content": dict(reversed(tuple(first.content.items())))}
    )

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_content_hash_excludes_first_seen_time() -> None:
    first = _post(_account().id)
    rediscovered = first.model_copy(
        update={"first_seen_at": FIRST_SEEN + timedelta(days=5)}
    )

    assert rediscovered.content_hash == first.content_hash


def test_content_hash_excludes_observation_drift() -> None:
    first = _post(_account().id)
    rediscovered = first.model_copy(
        update={
            "content": first.content
            | {
                "num_likes": 99,
                "num_comments": 12,
                "user_followers": 5000,
                "top_visible_comments": [{"text": "New comment"}],
            }
        }
    )

    assert rediscovered.content_hash == first.content_hash


def test_repeated_unchanged_merge_preserves_first_seen_and_bytes() -> None:
    existing = _post(_account().id)
    rediscovered = existing.model_copy(
        update={"first_seen_at": FIRST_SEEN + timedelta(days=5)}
    )

    merged = merge_post(existing, rediscovered)

    assert merged is existing
    assert merged.first_seen_at is FIRST_SEEN
    assert canonical_json_bytes(merged) == canonical_json_bytes(existing)


def test_merge_accepts_observation_drift_and_preserves_first_record() -> None:
    existing = _post(_account().id)
    rediscovered = existing.model_copy(
        update={
            "content": existing.content | {"num_likes": 99},
            "first_seen_at": FIRST_SEEN + timedelta(days=5),
        }
    )

    assert merge_post(existing, rediscovered) is existing


def test_merge_rejects_conflicting_content_for_one_post_id() -> None:
    account_model = _account()
    existing = _post(account_model.id)
    conflicting = existing.model_copy(
        update={
            "content": {"text": "different"},
            "first_seen_at": FIRST_SEEN + timedelta(days=5),
        }
    )

    with pytest.raises(PostMergeConflictError) as captured:
        _ = merge_post(existing, conflicting)

    assert captured.value.post_id == existing.id


def test_record_filename_is_safe_for_malicious_external_ids() -> None:
    filename = record_filename(PostId("linkedin:post:../../?token=canary\n"))

    assert len(filename) == 69
    assert filename.endswith(".json")
    assert filename[:-5].isalnum()
    assert filename[:-5] == filename[:-5].lower()
