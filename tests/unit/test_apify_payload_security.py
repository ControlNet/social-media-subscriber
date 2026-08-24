from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from pydantic import ValidationError

from social_media_subscriber.providers.apify.models import ApifyPost

if TYPE_CHECKING:
    from social_media_subscriber.serialization.json import JsonValue

_CANARY: Final = "EXPLICIT_NEGATIVE_TEST_CREDENTIAL_CANARY"


def _post_payload() -> dict[str, JsonValue]:
    return {
        "author": {
            "linkedinUrl": "https://www.linkedin.com/in/synthetic-ada/",
        },
        "content": "Synthetic post",
        "id": "1001",
        "linkedinUrl": (
            "https://www.linkedin.com/posts/synthetic-ada_example-activity-1001-abcd"
        ),
        "postedAt": {"date": "2026-08-19T09:00:00.000Z"},
        "type": "post",
    }


def test_apify_keeps_content_header_and_discards_query_metadata() -> None:
    header: dict[str, JsonValue] = {
        "image": "https://media.licdn.com/synthetic-image",
        "imageLink": "https://www.linkedin.com/synthetic-link",
        "linkedinUrl": "https://www.linkedin.com/in/synthetic-ada/",
        "text": "Synthetic header",
    }
    query: dict[str, JsonValue] = {
        "page": 1,
        "sessionId": _CANARY,
        "sortBy": "date",
        "targetUrl": "https://www.linkedin.com/in/synthetic-ada/",
    }

    post = ApifyPost.model_validate(
        _post_payload() | {"header": header, "query": query}
    )

    assert post.query is not None
    assert post.query.target_url == query["targetUrl"]
    assert post.payload["header"] == header
    assert "query" not in post.payload
    assert _CANARY not in post.model_dump_json()


def test_apify_rejects_sensitive_transport_field_inside_content_header() -> None:
    payload = _post_payload() | {
        "header": {"text": "Synthetic header", "Authorization": _CANARY}
    }

    with pytest.raises(ValidationError) as captured:
        _ = ApifyPost.model_validate(payload)

    assert _CANARY not in str(captured.value)
    assert _CANARY not in repr(captured.value)
