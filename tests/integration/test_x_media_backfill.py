from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, final

import pytest
from typer.testing import CliRunner

from social_media_subscriber.accounts.locator import parse_x_locator
from social_media_subscriber.application.x_media_backfill import (
    XMediaBackfillCommand,
    XMediaBackfillInputError,
    backfill_x_media,
)
from social_media_subscriber.cli import create_app
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import PlatformPostId
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.domain.post import Post
from social_media_subscriber.providers.x_syndication import (
    SyndicationFetchResult,
    SyndicationMissCategory,
    SyndicationTweet,
    XMediaEnricher,
)
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotState

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _account() -> Account:
    locator = parse_x_locator("https://x.com/synthetic_ada/")
    return Account(
        platform=Platform.X,
        kind=locator.kind,
        profile_url=locator.canonical_url,
        first_seen_at=_NOW,
    )


def _reply(identifier: str, parent: str) -> Post:
    return Post(
        platform_post_id=PlatformPostId(identifier),
        account_profile_url=_account().id,
        canonical_url=f"https://x.com/synthetic_ada/status/{identifier}",
        published_at=_NOW,
        type="reply",
        content={"text": "A reply", "inReplyToId": parent},
        first_seen_at=_NOW,
    )


def _tweet(identifier: str) -> SyndicationTweet:
    return SyndicationTweet.model_validate(
        {
            "id_str": identifier,
            "created_at": "2026-06-06T17:29:01.000Z",
            "text": "Referenced post",
            "user": {
                "name": "Referenced User",
                "screen_name": "referenced_user",
                "profile_image_url_https": "https://pbs.twimg.com/avatar.jpg",
            },
            "mediaDetails": [
                {
                    "type": "photo",
                    "media_url_https": "https://pbs.twimg.com/media/photo.jpg",
                    "url": "https://t.co/media",
                    "original_info": {"width": 1200, "height": 600},
                }
            ],
        }
    )


@final
@dataclass(slots=True)
class FakeClient:
    results: dict[str, SyndicationFetchResult]
    requests: list[str] = field(default_factory=list)
    close_calls: int = 0

    async def fetch(self, status_id: str) -> SyndicationFetchResult:
        self.requests.append(status_id)
        return self.results[status_id]

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.mark.anyio
async def test_backfill_writes_complete_candidate_and_allows_misses(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    account = _account()
    success = _reply("1001", "9001")
    missed = _reply("1002", "9002")
    _ = SnapshotRepository(source).write(SnapshotState((account,), (success, missed)))
    client = FakeClient(
        {
            "9001": SyndicationFetchResult(_tweet("9001"), None),
            "9002": SyndicationFetchResult(None, SyndicationMissCategory.NOT_FOUND),
        }
    )

    result = await backfill_x_media(
        XMediaBackfillCommand(source, candidate),
        enricher=XMediaEnricher(client),
    )

    written = SnapshotRepository(candidate).load_optional()
    assert written is not None
    assert written.accounts == (account,)
    assert written.posts[0].first_seen_at == success.first_seen_at
    assert "quotedTweet" in written.posts[0].content
    assert written.posts[1] == missed
    assert result.scanned_posts == 2
    assert result.eligible_posts == 2
    assert result.enriched_posts == 1
    assert result.missed_posts == 1
    assert result.media_items == 1
    assert result.digest
    assert client.requests == ["9001", "9002"]
    assert client.close_calls == 1


@pytest.mark.anyio
async def test_backfill_rejects_same_path_and_invalid_input(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    client = FakeClient({})

    with pytest.raises(XMediaBackfillInputError):
        _ = await backfill_x_media(
            XMediaBackfillCommand(missing, missing),
            enricher=XMediaEnricher(client),
        )

    with pytest.raises(SnapshotIntegrityError):
        _ = await backfill_x_media(
            XMediaBackfillCommand(missing, tmp_path / "candidate"),
            enricher=XMediaEnricher(client),
        )


def test_backfill_cli_maps_input_and_integrity_failures(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    candidate = tmp_path / "candidate"

    same_path = CliRunner().invoke(
        create_app(),
        [
            "enrich-x-media",
            "--snapshot",
            str(missing),
            "--output",
            str(missing),
        ],
    )
    invalid_snapshot = CliRunner().invoke(
        create_app(),
        [
            "enrich-x-media",
            "--snapshot",
            str(missing),
            "--output",
            str(candidate),
        ],
    )

    assert same_path.exit_code == 2
    assert '"error_category":"input"' in same_path.output
    assert invalid_snapshot.exit_code == 5
    assert '"error_category":"integrity"' in invalid_snapshot.output
    assert not candidate.exists()
