from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest

from social_media_subscriber.domain import Account, AccountKind, Platform
from social_media_subscriber.domain.ids import PlatformPostId, record_filename
from social_media_subscriber.domain.post import Post
from social_media_subscriber.domain.post_index import PostsIndex
from social_media_subscriber.serialization.json import (
    JsonBoundaryModel,
    canonical_json_bytes,
)
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from pathlib import Path

NOW: Final = datetime(2026, 8, 20, 12, tzinfo=UTC)
ACCOUNT_URL: Final = "https://www.linkedin.com/in/synthetic-ada/"
X_ACCOUNT_URL: Final = "https://x.com/synthetic_x/"


def _state() -> SnapshotState:
    account = Account(
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=ACCOUNT_URL,
        first_seen_at=NOW,
    )
    post = Post(
        platform_post_id=PlatformPostId("urn:li:activity:1001"),
        account_profile_url=account.id,
        canonical_url="https://www.linkedin.com/posts/synthetic-1001/",
        published_at=NOW,
        type="post",
        content={
            "text": "Synthetic",
            "images": ["https://media.licdn.com/synthetic"],
            "num_likes": 1,
            "links": [],
        },
        first_seen_at=NOW,
    )
    return SnapshotState((account,), (post,))


def _mixed_state() -> SnapshotState:
    linkedin = _state()
    x_account = Account(
        platform=Platform.X,
        kind=AccountKind.PROFILE,
        profile_url=X_ACCOUNT_URL,
        first_seen_at=NOW,
    )
    x_post = Post(
        platform_post_id=PlatformPostId("1001"),
        account_profile_url=x_account.id,
        canonical_url="https://x.com/synthetic_x/status/1001",
        published_at=NOW + timedelta(minutes=1),
        type="post",
        content={"text": "Synthetic X post"},
        first_seen_at=NOW,
    )
    return SnapshotState((*linkedin.accounts, x_account), (*linkedin.posts, x_post))


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def storage_state() -> SnapshotState:
    return _state()


def tree_bytes(root: Path) -> dict[str, bytes]:
    return _tree(root)


def test_repository_writes_only_flat_index_accounts_and_posts(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    state = _state()

    summary = SnapshotRepository(root).write(state)

    assert SnapshotRepository(root).load_optional() == state
    assert summary.account_count == summary.post_count == 1
    assert set(_tree(root)) == {
        f"accounts/{record_filename(state.accounts[0].id)}",
        f"posts/linkedin/{record_filename(state.posts[0].id)}",
        "accounts.json",
        "posts.json",
    }
    index = PostsIndex.model_validate_json((root / "posts.json").read_bytes())
    assert len(index.posts) == 1
    assert index.posts[0].path == (
        f"posts/linkedin/{record_filename(state.posts[0].id)}"
    )
    assert index.posts[0].account_profile_url == ACCOUNT_URL
    assert index.posts[0].published_at == NOW
    assert index.posts[0].platform.value == "linkedin"


def test_repository_repeated_write_is_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    state = _state()
    _ = SnapshotRepository(root).write(state)
    before = _tree(root)

    second = SnapshotRepository(root).write(state)
    validated = SnapshotRepository(root).read_optional()

    assert _tree(root) == before
    assert validated is not None
    assert second.digest == validated.summary.digest


def test_repository_writes_mixed_platform_posts_without_changing_linkedin_records(
    tmp_path: Path,
) -> None:
    # Given
    linkedin_root = tmp_path / "linkedin"
    mixed_root = tmp_path / "mixed"
    linkedin_state = _state()
    mixed_state = _mixed_state()
    _ = SnapshotRepository(linkedin_root).write(linkedin_state)
    linkedin_tree = _tree(linkedin_root)

    # When
    _ = SnapshotRepository(mixed_root).write(mixed_state)

    # Then
    mixed_tree = _tree(mixed_root)
    linkedin_post_path = f"posts/linkedin/{record_filename(linkedin_state.posts[0].id)}"
    x_post_path = f"posts/x/{record_filename(mixed_state.posts[1].id)}"
    assert SnapshotRepository(mixed_root).load_optional() == mixed_state
    assert linkedin_post_path in mixed_tree
    assert x_post_path in mixed_tree
    assert mixed_tree[linkedin_post_path] == linkedin_tree[linkedin_post_path]
    index = PostsIndex.model_validate_json((mixed_root / "posts.json").read_bytes())
    assert tuple((item.platform, item.path) for item in index.posts) == (
        (Platform.X, x_post_path),
        (Platform.LINKEDIN, linkedin_post_path),
    )


def test_posts_index_is_newest_first_and_empty_safe(tmp_path: Path) -> None:
    state = _state()
    first = state.posts[0]
    newer = first.model_copy(
        update={
            "platform_post_id": PlatformPostId("1002"),
            "canonical_url": (
                "https://www.linkedin.com/feed/update/urn:li:activity:1002/"
            ),
            "published_at": NOW + timedelta(hours=1),
        }
    )
    populated = tmp_path / "populated"
    empty = tmp_path / "empty"

    _ = SnapshotRepository(populated).write(
        SnapshotState(state.accounts, (first, newer))
    )
    _ = SnapshotRepository(empty).write(SnapshotState(state.accounts, ()))

    populated_index = PostsIndex.model_validate_json(
        (populated / "posts.json").read_bytes()
    )
    empty_index = PostsIndex.model_validate_json((empty / "posts.json").read_bytes())
    assert tuple(item.path for item in populated_index.posts) == (
        f"posts/linkedin/{record_filename(newer.id)}",
        f"posts/linkedin/{record_filename(first.id)}",
    )
    assert empty_index.posts == ()


def test_repository_replaces_an_empty_output_placeholder(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    root.mkdir()

    _ = SnapshotRepository(root).write(_state())

    assert SnapshotRepository(root).load_optional() == _state()


@pytest.mark.parametrize(
    "target",
    ["accounts.json", "posts.json", "accounts/*.json", "posts/linkedin/*.json"],
)
def test_repository_rejects_corrupt_inventory(tmp_path: Path, target: str) -> None:
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(_state())
    record = next(root.glob(target))
    _ = record.write_bytes(b"{}\n")

    with pytest.raises(SnapshotIntegrityError):
        _ = SnapshotRepository(root).load_optional()


def test_repository_rejects_unexpected_derived_files(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(_state())
    _ = (root / "snapshot.json").write_bytes(b"{}\n")

    with pytest.raises(SnapshotIntegrityError):
        _ = SnapshotRepository(root).load_optional()


def test_failed_candidate_encoding_preserves_prior_bytes(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    state = _state()
    _ = SnapshotRepository(root).write(state)
    before = _tree(root)
    calls = 0

    def fail_after_first(model: JsonBoundaryModel) -> bytes:
        nonlocal calls
        calls += 1
        if calls > 1:
            message = "synthetic encoder failure"
            raise RuntimeError(message)
        return canonical_json_bytes(model)

    with pytest.raises(SnapshotIntegrityError):
        _ = SnapshotRepository(root, fail_after_first).write(state)

    assert _tree(root) == before
