from __future__ import annotations

from typing import Final

import pytest
from pydantic import ValidationError

from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    JsonValue,
)

_CANARY: Final = "EXPLICIT_NEGATIVE_TEST_CREDENTIAL_CANARY"


def _post_payload() -> dict[str, JsonValue]:
    return {
        "id": "urn:li:activity:synthetic-payload-security",
        "date_posted": "2026-08-20T12:00:00+00:00",
        "post_type": "post",
        "url": "https://www.linkedin.com/posts/synthetic-payload-security/",
        "use_url": "https://www.linkedin.com/in/synthetic-ada/",
    }


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "apiKey",
        "API-KEY",
        "api_key",
        "clientSecret",
        "CLIENT-SECRET",
        "client_secret",
        "cookie",
        "Authorization",
        "snapshot_id",
        "snapshotId",
    ],
)
def test_success_payload_rejects_normalized_sensitive_key_without_canary_leak(
    sensitive_key: str,
) -> None:
    # Given
    payload = _post_payload() | {sensitive_key: _CANARY}

    # When / Then
    with pytest.raises(ValidationError) as captured:
        _ = BrightDataPost.model_validate(payload)
    assert _CANARY not in str(captured.value)
    assert _CANARY not in repr(captured.value)


@pytest.mark.parametrize(
    "nested_metadata",
    [
        {"request": {"headers": {"authorization": _CANARY}}},
        {"transport": [{"Authorization": _CANARY}]},
        {"provider": {"client.secret": _CANARY}},
        {"provider": [{"API Key": _CANARY}]},
        {"session": {"cookies": [_CANARY]}},
    ],
)
def test_success_payload_rejects_sensitive_metadata_recursively_without_leak(
    nested_metadata: dict[str, JsonValue],
) -> None:
    # Given
    payload = _post_payload() | {"provider_metadata": nested_metadata}

    # When / Then
    with pytest.raises(ValidationError) as captured:
        _ = BrightDataPost.model_validate(payload)
    assert _CANARY not in str(captured.value)
    assert _CANARY not in repr(captured.value)


def test_success_payload_preserves_ordinary_nested_provider_content() -> None:
    # Given
    ordinary_content: dict[str, JsonValue] = {
        "campaign": {
            "label": "Synthetic launch",
            "metrics": [{"name": "impressions", "value": 42}],
        },
        "annotations": ["featured", {"locale": "en-US"}],
    }
    payload = _post_payload() | {"provider_details": ordinary_content}

    # When
    post = BrightDataPost.model_validate(payload)

    # Then
    assert post.payload["provider_details"] == ordinary_content
