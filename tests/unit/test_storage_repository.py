from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.domain import (
    Account,
    AccountKind,
    Platform,
    PlatformAccountId,
    PlatformPostId,
)
from social_media_subscriber.domain.ids import (
    account_id_for,
    post_id_for,
    record_filename,
)
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.serialization.json import canonical_json_bytes
from social_media_subscriber.storage.layout import MANIFEST, snapshot_digest
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotManifest, SnapshotState

if TYPE_CHECKING:
    from social_media_subscriber.serialization.json import JsonBoundaryModel

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _state() -> SnapshotState:
    platform_account_id = PlatformAccountId("12345")
    account = Account(
        id=account_id_for(AccountKind.PERSON, platform_account_id),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        platform_account_id=platform_account_id,
        profile_url="https://www.linkedin.com/in/synthetic-ada/",
        url_aliases=(),
        first_seen_at=NOW,
    )
    platform_post_id = PlatformPostId("urn:li:activity:1001")
    post = Post.from_stable(
        StablePostContent(
            schema_version=1,
            id=post_id_for(platform_post_id),
            platform_post_id=platform_post_id,
            account_id=account.id,
            canonical_url="https://www.linkedin.com/posts/synthetic-1001/",
            published_at=NOW,
            text="Synthetic",
            kind=PostKind.ORIGINAL,
            hashtags=(),
            links=(),
        ),
        NOW,
    )
    provider = BrightDataPost.model_validate(
        {
            "id": str(platform_post_id),
            "date_posted": "2026-08-20T12:00:00+00:00",
            "post_type": "post",
            "url": post.canonical_url,
            "user_id": str(platform_account_id),
            "num_likes": 1,
        }
    )
    source = BrightDataLinkedInPostSourceRecord.from_post(account.id, provider)
    return SnapshotState((account,), (post,), (source,))


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_repository_writes_exact_deterministic_tree_and_reloads(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    state = _state()

    # When
    manifest = repository.write(state)
    loaded = repository.load_optional()

    # Then
    account = state.accounts[0]
    post = state.posts[0]
    assert set(_tree(root)) == {
        f"accounts/{record_filename(account.id)}",
        f"posts/linkedin/{record_filename(post.id)}",
        f"source/brightdata/linkedin/posts/{record_filename(post.id)}",
        "accounts.json",
        "feed.json",
        "snapshot.json",
    }
    assert (
        manifest.account_count
        == manifest.post_count
        == manifest.source_record_count
        == 1
    )
    assert loaded == state


def test_repository_repeated_write_is_byte_identical(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    state = _state()
    _ = repository.write(state)
    before = _tree(root)

    # When
    _ = repository.write(
        SnapshotState(
            state.accounts[::-1], state.posts[::-1], state.source_records[::-1]
        )
    )

    # Then
    assert _tree(root) == before


def test_feed_orders_newest_then_post_id(tmp_path: Path) -> None:
    # Given
    base = _state()
    account = base.accounts[0]
    older_id = PlatformPostId("z-post")
    newer_id = PlatformPostId("a-post")
    older = Post.from_stable(
        StablePostContent(
            1,
            post_id_for(older_id),
            older_id,
            account.id,
            "https://www.linkedin.com/posts/older/",
            NOW.replace(hour=10),
            None,
            PostKind.ORIGINAL,
            (),
            (),
        ),
        NOW,
    )
    newer = Post.from_stable(
        StablePostContent(
            1,
            post_id_for(newer_id),
            newer_id,
            account.id,
            "https://www.linkedin.com/posts/newer/",
            NOW.replace(hour=11),
            None,
            PostKind.ORIGINAL,
            (),
            (),
        ),
        NOW,
    )
    repository = SnapshotRepository(tmp_path / "dist")

    # When
    _ = repository.write(SnapshotState((account,), (older, newer), ()))

    # Then
    feed = (tmp_path / "dist" / "feed.json").read_text()
    assert feed.index(str(newer.id)) < feed.index(str(older.id))


@pytest.mark.parametrize("target", ["snapshot.json", "accounts.json", "feed.json"])
def test_repository_rejects_corrupt_prior_files(tmp_path: Path, target: str) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    _ = repository.write(_state())
    _ = (root / target).write_bytes(b"{}\n")

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.load_optional()


def test_repository_rejects_corrupt_record_even_with_manifest_rewritten(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    state = _state()
    _ = repository.write(state)
    record = root / "accounts" / record_filename(state.accounts[0].id)
    _ = record.write_bytes(b"{}\n")
    non_manifest = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root) != MANIFEST
    }
    prior_manifest = SnapshotManifest.model_validate_json(
        (root / MANIFEST).read_bytes()
    )
    rewritten = prior_manifest.model_copy(
        update={"digest": snapshot_digest(non_manifest)}
    )
    _ = (root / MANIFEST).write_bytes(canonical_json_bytes(rewritten))

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.load_optional()


def test_serialization_failure_never_promotes_candidate(tmp_path: Path) -> None:
    # Given
    root = tmp_path / "dist"
    good = SnapshotRepository(root)
    _ = good.write(_state())
    before = _tree(root)

    def fail_encoding(model: JsonBoundaryModel) -> bytes:
        _ = model
        message = "injected serialization interruption"
        raise OSError(message)

    failing = SnapshotRepository(root, encoder=fail_encoding)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = failing.write(_state())
    assert _tree(root) == before
    assert good.load_optional() == _state()


def test_runtime_encoding_failure_is_typed_and_preserves_prior_tree(
    tmp_path: Path,
) -> None:
    # Given
    root = tmp_path / "dist"
    good = SnapshotRepository(root)
    _ = good.write(_state())
    before = _tree(root)

    def fail_encoding(model: JsonBoundaryModel) -> bytes:
        _ = model
        message = "injected runtime serialization interruption"
        raise RuntimeError(message)

    failing = SnapshotRepository(root, encoder=fail_encoding)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = failing.write(SnapshotState((), (), ()))
    assert _tree(root) == before
    assert good.load_optional() == _state()


def test_partial_write_failure_never_promotes_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    _ = repository.write(_state())
    before = _tree(root)
    original_write = Path.write_bytes
    writes = 0

    def interrupted_write(path: Path, payload: bytes) -> int:
        nonlocal writes
        writes += 1
        if writes == 3:
            message = "injected partial write interruption"
            raise OSError(message)
        return original_write(path, payload)

    monkeypatch.setattr(Path, "write_bytes", interrupted_write)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(_state())
    assert _tree(root) == before


def test_double_promotion_interruption_restores_the_prior_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    root = tmp_path / "dist"
    repository = SnapshotRepository(root)
    state = _state()
    _ = repository.write(state)
    before = _tree(root)
    original_replace = Path.replace

    def interrupted_replace(path: Path, target: Path) -> Path:
        if target == root and ".previous." not in path.name:
            message = "injected candidate promotion interruption"
            raise OSError(message)
        if target == root and ".previous." in path.name:
            message = "injected rollback promotion interruption"
            raise OSError(message)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", interrupted_replace)

    # When / Then
    with pytest.raises(SnapshotIntegrityError):
        _ = repository.write(SnapshotState((), (), ()))
    assert root.is_dir()
    assert _tree(root) == before
    assert repository.load_optional() == state
    assert list(tmp_path.glob(".dist.*")) == []


def test_record_paths_are_hash_derived_and_cannot_escape(tmp_path: Path) -> None:
    # Given
    malicious_id = PlatformPostId("../../escape")
    state = _state()
    account = state.accounts[0]
    post = Post.from_stable(
        StablePostContent(
            1,
            post_id_for(malicious_id),
            malicious_id,
            account.id,
            "https://www.linkedin.com/posts/safe/",
            NOW,
            None,
            PostKind.ORIGINAL,
            (),
            (),
        ),
        NOW,
    )
    root = tmp_path / "dist"

    # When
    _ = SnapshotRepository(root).write(SnapshotState((account,), (post,), ()))

    # Then
    filenames = [path.name for path in (root / "posts" / "linkedin").iterdir()]
    assert filenames == [f"{hashlib.sha256(str(post.id).encode()).hexdigest()}.json"]
    assert not (tmp_path / "escape").exists()
