from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from social_media_subscriber.accounts.locator import parse_x_locator
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import Platform
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.x_models import (
    ApifyXDiagnostic,
    ApifyXPost,
)
from social_media_subscriber.providers.apify.x_normalize import normalize_posts
from social_media_subscriber.providers.apify.x_requests import ApifyXPostInput

_FIXTURE = Path(__file__).parents[1] / "fixtures/apify/synthetic-x-original.json"
_FIRST_SEEN = datetime(2026, 8, 20, 12, tzinfo=UTC)
_PROFILE_URL = "https://x.com/synthetic_ada/"


def _record() -> ApifyXPost:
    return ApifyXPost.model_validate_json(_FIXTURE.read_bytes())


def _account(profile_url: str = _PROFILE_URL) -> Account:
    locator = parse_x_locator(profile_url)
    return Account(
        platform=Platform.X,
        kind=locator.kind,
        profile_url=locator.canonical_url,
        first_seen_at=_FIRST_SEEN,
    )


def test_x_incremental_request_uses_exact_bounded_latest_search_contract() -> None:
    request = ApifyXPostInput(
        _PROFILE_URL,
        date(2026, 8, 17),
        date(2026, 8, 20),
        is_initial_collection=False,
    )

    assert request.as_json() == {
        "fieldStyle": "camelCase",
        "mode": "search",
        "outputPreset": "nested",
        "outputVariant": "rich",
        "queryType": "Latest",
        "searchTerms": ["from:synthetic_ada since:2026-08-17 until:2026-08-21"],
    }


def test_x_history_request_uses_exact_unbounded_profile_replies_contract() -> None:
    request = ApifyXPostInput(
        _PROFILE_URL,
        date(2006, 3, 21),
        date(2026, 8, 29),
        is_initial_collection=True,
    )

    assert request.as_json() == {
        "fieldStyle": "camelCase",
        "mode": "profileReplies",
        "outputPreset": "nested",
        "outputVariant": "rich",
        "twitterHandles": ["synthetic_ada"],
    }


def test_xquik_record_preserves_open_rich_payload_without_transport_metadata() -> None:
    record = _record()

    assert record.id == "2001"
    assert record.author.username == "synthetic_ada"
    assert record.timestamp == datetime(2026, 8, 19, 9, tzinfo=UTC)
    assert record.payload["entities"] == {
        "hashtags": [{"text": "Synthetic"}],
        "urls": [],
        "userMentions": [],
    }
    assert record.payload["edit"] == {
        "editableUntil": "2026-08-19T10:00:00.000Z",
        "isEditEligible": False,
    }


def test_xquik_record_rejects_nested_sensitive_transport_fields() -> None:
    payload = _record().payload
    payload["entities"] = {"requestHeaders": {"Authorization": "canary"}}

    with pytest.raises(ValidationError) as captured:
        _ = ApifyXPost.model_validate(payload)

    assert "canary" not in str(captured.value)
    assert "canary" not in repr(captured.value)


def test_xquik_zero_output_diagnostic_rejects_tweet_fields() -> None:
    with pytest.raises(ValidationError) as captured:
        _ = ApifyXDiagnostic.model_validate(
            {
                "id": "diag:zero-output:synthetic",
                "resultType": "diagnostic",
                "status": "zero-output",
                "text": "private-canary",
            }
        )

    assert "private-canary" not in str(captured.value)
    assert "private-canary" not in repr(captured.value)


def test_x_normalizer_builds_canonical_post_and_preserves_rich_content() -> None:
    post = normalize_posts(_account(), (_record(),), _FIRST_SEEN)[0]

    assert post.platform is Platform.X
    assert post.platform_post_id == "2001"
    assert post.account_profile_url == _PROFILE_URL
    assert post.canonical_url == "https://x.com/synthetic_ada/status/2001"
    assert post.published_at == datetime(2026, 8, 19, 9, tzinfo=UTC)
    assert post.type == "post"
    assert post.content["text"] == "Synthetic X post"
    assert post.content["engagement"] == {
        "bookmarks": 1,
        "likes": 7,
        "quotes": 2,
        "replies": 3,
        "reposts": 5,
        "views": 101,
    }
    assert post.content["author"] == _record().payload["author"]
    assert post.content["entities"] == _record().payload["entities"]
    assert (
        not {
            "bookmarkCount",
            "createdAt",
            "id",
            "likeCount",
            "quoteCount",
            "replyCount",
            "retweetCount",
            "type",
            "url",
            "viewCount",
        }
        & post.content.keys()
    )


@pytest.mark.parametrize(
    ("provider_type", "is_reply", "is_quote", "expected"),
    [
        ("tweet", False, False, "post"),
        ("tweet", False, True, "quote"),
        ("reply", True, False, "reply"),
    ],
)
def test_x_normalizer_derives_supported_post_type(
    provider_type: str,
    is_reply: bool,
    is_quote: bool,
    expected: str,
) -> None:
    payload = _record().payload | {
        "type": provider_type,
        "isReply": is_reply,
        "isQuoteStatus": is_quote,
    }

    post = normalize_posts(
        _account(),
        (ApifyXPost.model_validate(payload),),
        _FIRST_SEEN,
    )[0]

    assert post.type == expected


def test_x_normalizer_rejects_actor_ownership_mismatch_without_leaking_handle() -> None:
    payload = _record().payload
    author = payload["author"]
    assert isinstance(author, dict)
    payload["author"] = author | {"username": "private-canary"}
    record = ApifyXPost.model_validate(payload)

    with pytest.raises(ApifyError) as captured:
        _ = normalize_posts(_account(), (record,), _FIRST_SEEN)

    assert captured.value.category is ApifyErrorCategory.OWNERSHIP
    assert "private-canary" not in str(captured.value)


def test_x_normalizer_rejects_conflicting_duplicate_content() -> None:
    original = _record()
    conflicting = ApifyXPost.model_validate(original.payload | {"text": "Changed"})

    with pytest.raises(ApifyError) as captured:
        _ = normalize_posts(_account(), (original, conflicting), _FIRST_SEEN)

    assert captured.value.category is ApifyErrorCategory.DUPLICATE
