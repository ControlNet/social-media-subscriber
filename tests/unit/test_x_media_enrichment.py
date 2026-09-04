from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, final

import pytest

from social_media_subscriber.accounts.locator import parse_x_locator
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

if TYPE_CHECKING:
    from social_media_subscriber.providers.x_syndication import (
        SyndicationClientContract,
    )
    from social_media_subscriber.serialization.json import JsonValue

_NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
_PROFILE_URL = "https://x.com/synthetic_ada/"


def _account() -> Account:
    locator = parse_x_locator(_PROFILE_URL)
    return Account(
        platform=Platform.X,
        kind=locator.kind,
        profile_url=locator.canonical_url,
        first_seen_at=_NOW,
    )


def _post(
    identifier: str,
    *,
    post_type: str = "post",
    text: str = "Original post",
    content: dict[str, JsonValue] | None = None,
) -> Post:
    return Post(
        platform_post_id=PlatformPostId(identifier),
        account_profile_url=_account().id,
        canonical_url=f"https://x.com/synthetic_ada/status/{identifier}",
        published_at=_NOW,
        type=post_type,
        content={"text": text} if content is None else content,
        first_seen_at=_NOW,
    )


def _response(
    identifier: str = "9001", username: str = "referenced_user"
) -> SyndicationTweet:
    return SyndicationTweet.model_validate(
        {
            "id_str": identifier,
            "created_at": "2026-06-06T17:29:01.000Z",
            "text": "Referenced post https://t.co/media1",
            "user": {
                "name": "Referenced User",
                "screen_name": username,
                "profile_image_url_https": (
                    "https://pbs.twimg.com/profile_images/avatar_normal.jpg"
                ),
            },
            "mediaDetails": [
                {
                    "type": "photo",
                    "media_url_https": "https://pbs.twimg.com/media/photo.jpg",
                    "url": "https://t.co/media1",
                    "ext_alt_text": "A synthetic diagram",
                    "original_info": {"width": 1200, "height": 600},
                },
                {
                    "type": "video",
                    "media_url_https": "https://pbs.twimg.com/media/video.jpg",
                    "url": "https://t.co/media2",
                    "original_info": {"width": 1280, "height": 720},
                    "video_info": {
                        "variants": [
                            {
                                "bitrate": 256000,
                                "content_type": "video/mp4",
                                "url": "https://video.twimg.com/ext_tw_video/low.mp4",
                            },
                            {
                                "content_type": "application/x-mpegURL",
                                "url": "https://video.twimg.com/ext_tw_video/list.m3u8",
                            },
                            {
                                "bitrate": 832000,
                                "content_type": "video/mp4",
                                "url": "https://video.twimg.com/ext_tw_video/high.mp4",
                            },
                            {
                                "bitrate": 999999,
                                "content_type": "video/mp4",
                                "url": "https://evil.example/video.mp4",
                            },
                        ]
                    },
                },
                {
                    "type": "animated_gif",
                    "media_url_https": "https://pbs.twimg.com/media/gif.jpg",
                    "url": "https://t.co/media3",
                    "original_info": {"width": 640, "height": 360},
                    "video_info": {
                        "variants": [
                            {
                                "content_type": "video/mp4",
                                "url": "https://video.twimg.com/tweet_video/gif.mp4",
                            }
                        ]
                    },
                },
                {
                    "type": "photo",
                    "media_url_https": "https://evil.example/rejected.jpg",
                    "url": "https://t.co/rejected",
                    "original_info": {"width": 10, "height": 10},
                },
            ],
        }
    )


@final
@dataclass(slots=True)
class FakeSyndicationClient:
    responses: dict[str, SyndicationFetchResult]
    requests: list[str] = field(default_factory=list)
    close_calls: int = 0

    async def fetch(self, status_id: str) -> SyndicationFetchResult:
        self.requests.append(status_id)
        return self.responses[status_id]

    async def aclose(self) -> None:
        self.close_calls += 1


def _enricher(client: SyndicationClientContract) -> XMediaEnricher:
    return XMediaEnricher(client)


@pytest.mark.anyio
async def test_reply_enrichment_projects_media_and_deduplicates_parent() -> None:
    response = SyndicationFetchResult(_response(), None)
    client = FakeSyndicationClient({"9001": response})
    first = _post(
        "1001",
        post_type="reply",
        content={"text": "First reply", "inReplyToId": "9001"},
    )
    second = _post(
        "1002",
        post_type="reply",
        content={"text": "Second reply", "inReplyToId": "9001"},
    )

    result = await _enricher(client).enrich((first, second))

    assert client.requests == ["9001"]
    assert result.report.scanned_posts == 2
    assert result.report.eligible_posts == 2
    assert result.report.enriched_posts == 2
    assert result.report.missed_posts == 0
    assert result.report.media_items == 6
    quoted = result.posts[0].content["quotedTweet"]
    assert isinstance(quoted, dict)
    assert quoted == {
        "id": "9001",
        "url": "https://x.com/referenced_user/status/9001",
        "createdAt": "2026-06-06T17:29:01.000Z",
        "text": "Referenced post https://t.co/media1",
        "author": {
            "name": "Referenced User",
            "username": "referenced_user",
            "profilePicture": (
                "https://pbs.twimg.com/profile_images/avatar_normal.jpg"
            ),
        },
        "media": [
            {
                "type": "photo",
                "mediaUrl": "https://pbs.twimg.com/media/photo.jpg",
                "width": 1200,
                "height": 600,
                "url": "https://t.co/media1",
                "altText": "A synthetic diagram",
            },
            {
                "type": "video",
                "mediaUrl": "https://pbs.twimg.com/media/video.jpg",
                "width": 1280,
                "height": 720,
                "url": "https://t.co/media2",
                "videoVariants": [
                    {
                        "bitrate": 256000,
                        "contentType": "video/mp4",
                        "url": "https://video.twimg.com/ext_tw_video/low.mp4",
                    },
                    {
                        "bitrate": 832000,
                        "contentType": "video/mp4",
                        "url": "https://video.twimg.com/ext_tw_video/high.mp4",
                    },
                ],
            },
            {
                "type": "animated_gif",
                "mediaUrl": "https://pbs.twimg.com/media/gif.jpg",
                "width": 640,
                "height": 360,
                "url": "https://t.co/media3",
                "videoVariants": [
                    {
                        "contentType": "video/mp4",
                        "url": "https://video.twimg.com/tweet_video/gif.mp4",
                    }
                ],
            },
        ],
    }


@pytest.mark.anyio
async def test_selection_integrity_and_best_effort_preserve_original_posts() -> None:
    client = FakeSyndicationClient(
        {
            "2001": SyndicationFetchResult(_response("8001", "source_user"), None),
            "9002": SyndicationFetchResult(_response("wrong-id"), None),
            "2003": SyndicationFetchResult(_response("8003", "wrong_user"), None),
            "9004": SyndicationFetchResult(None, SyndicationMissCategory.RATE_LIMIT),
        }
    )
    repost = _post("2001", text="RT @source_user: original")
    mismatched_reply = _post(
        "2002",
        post_type="reply",
        content={"text": "reply", "inReplyToId": "9002"},
    )
    mismatched_repost = _post("2003", text="RT @source_user: original")
    failed_reply = _post(
        "2004",
        post_type="reply",
        content={"text": "reply", "inReplyToId": "9004"},
    )
    already = _post("2005", content={"text": "post", "quotedTweet": {"id": "old"}})
    quote = _post("2006", post_type="quote")
    plain = _post("2007")
    loose_repost = _post("2008", text="RT @bad-handle: original")

    result = await _enricher(client).enrich(
        (
            repost,
            mismatched_reply,
            mismatched_repost,
            failed_reply,
            already,
            quote,
            plain,
            loose_repost,
        )
    )

    assert client.requests == ["2001", "9002", "2003", "9004"]
    assert result.report.scanned_posts == 8
    assert result.report.eligible_posts == 4
    assert result.report.enriched_posts == 1
    assert result.report.missed_posts == 3
    assert "quotedTweet" in result.posts[0].content
    assert result.posts[1:4] == (mismatched_reply, mismatched_repost, failed_reply)
    assert result.posts[4:] == (already, quote, plain, loose_repost)


@pytest.mark.anyio
async def test_invalid_reply_reference_is_an_eligible_miss_without_request() -> None:
    client = FakeSyndicationClient({})
    post = _post(
        "3001",
        post_type="reply",
        content={"text": "reply", "inReplyToId": "not-a-status-id"},
    )

    result = await _enricher(client).enrich((post,))

    assert client.requests == []
    assert result.posts == (post,)
    assert result.report.eligible_posts == result.report.missed_posts == 1
