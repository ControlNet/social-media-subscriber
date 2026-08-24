from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.domain.post_index import PostsIndex
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.normalize import normalize_posts
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.snapshot import SnapshotState

PROJECT_ROOT: Final = Path(__file__).parents[2]
PROFILE_URL: Final = "https://www.linkedin.com/in/synthetic-ada/"
FIRST_SEEN: Final = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _account() -> Account:
    return Account(
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=PROFILE_URL,
        first_seen_at=FIRST_SEEN,
    )


def _fixture(name: str) -> BrightDataPost:
    return BrightDataPost.model_validate_json(
        (PROJECT_ROOT / "tests" / "fixtures" / "brightdata" / name).read_bytes()
    )


def test_boundary_records_have_no_persisted_versions_or_duplicate_account_id() -> None:
    account = _account()
    post = normalize_posts(
        account, (_fixture("synthetic-person-original.json"),), FIRST_SEEN
    ).posts[0]

    account_json = account.model_dump(mode="json")
    post_json = post.model_dump(mode="json")

    assert account_json == {
        "platform": "linkedin",
        "kind": "person",
        "profile_url": PROFILE_URL,
        "first_seen_at": "2026-08-20T12:00:00Z",
    }
    assert "schema_version" not in post_json
    assert "id" not in post_json
    assert "content_hash" not in post_json


def test_normalization_preserves_safe_content_and_all_post_types() -> None:
    records = tuple(
        _fixture(name)
        for name in (
            "synthetic-person-original.json",
            "synthetic-person-reply.json",
            "synthetic-person-repost.json",
            "synthetic-person-quote.json",
            "synthetic-person-unknown.json",
        )
    )

    result = normalize_posts(_account(), records, FIRST_SEEN)

    assert tuple(post.type for post in result.posts) == (
        "post",
        "reply",
        "repost",
        "quote",
        "future_kind",
    )
    original = result.posts[0]
    assert original.content["text"] == "Synthetic original post"
    assert original.content["images"] == [
        {"url": "https://media.licdn.com/dms/image/synthetic?signature=redacted"}
    ]
    assert original.content["videos"] == [
        {"url": "https://media.licdn.com/video/synthetic"}
    ]
    assert original.content["headline"] == "Explicitly synthetic headline"
    assert original.content["title"] == "Explicitly synthetic title"
    assert original.content["engagement"] == {"comments": 3, "likes": 42}
    assert original.content["unknown_nested"] == {"future": [True, None, {"n": 3}]}
    assert original.content["hashtags"] == ["Testing", "Synthetic", "Testing"]
    assert (
        original.content["links"]
        == _fixture("synthetic-person-original.json").payload["embedded_links"]
    )


def test_repository_writes_only_accounts_and_posts_with_a_flat_account_index(
    tmp_path: Path,
) -> None:
    account = _account()
    posts = normalize_posts(
        account, (_fixture("synthetic-person-original.json"),), FIRST_SEEN
    ).posts
    root = tmp_path / "dist"

    _ = SnapshotRepository(root).write(SnapshotState((account,), posts))

    files = tuple(
        sorted(path.relative_to(root).as_posix() for path in root.rglob("*.json"))
    )
    assert len(files) == 4
    assert files[0] == "accounts.json"
    assert files[1].startswith("accounts/")
    assert files[2] == "posts.json"
    assert files[3].startswith("posts/linkedin/")
    assert not (root / "feed.json").exists()
    assert not (root / "source").exists()
    assert not (root / "snapshot.json").exists()

    account_index = TypeAdapter(dict[str, str]).validate_json(
        (root / "accounts.json").read_bytes()
    )
    assert account_index == {PROFILE_URL: files[1]}
    posts_index = PostsIndex.model_validate_json((root / "posts.json").read_bytes())
    assert posts_index.model_dump(mode="json") == {
        "posts": [
            {
                "path": files[3],
                "account_profile_url": PROFILE_URL,
                "published_at": "2026-08-19T09:30:00Z",
                "platform": "linkedin",
            }
        ]
    }
