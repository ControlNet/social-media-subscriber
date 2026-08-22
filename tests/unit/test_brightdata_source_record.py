from __future__ import annotations

import hashlib
from typing import Final, Literal

import pytest
from pydantic import ConfigDict, TypeAdapter, ValidationError

from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    JsonValue,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
    source_record_path,
)
from social_media_subscriber.serialization.json import canonical_json_value_bytes

_ACCOUNT_URL: Final = "https://www.linkedin.com/in/synthetic-ada/"
_CANARY: Final = "EXPLICIT_NEGATIVE_TEST_CREDENTIAL_CANARY"
_SENSITIVE_MARKERS: Final = (
    "snapshot_id",
    "snapshotId",
    "API Key",
    "authorization",
    "Authorization",
    "client_secret",
)
_JSON_OBJECT: Final[TypeAdapter[dict[str, JsonValue]]] = TypeAdapter(
    dict[str, JsonValue],
    config=ConfigDict(strict=True),
)


def _source_record() -> BrightDataLinkedInPostSourceRecord:
    provider_post = BrightDataPost.model_validate(
        {
            "id": "urn:li:activity:synthetic-1001",
            "date_posted": "2026-08-20T12:00:00+00:00",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-1001/",
            "use_url": _ACCOUNT_URL,
            "num_likes": 7,
        }
    )
    return BrightDataLinkedInPostSourceRecord.from_post(
        AccountId(_ACCOUNT_URL), provider_post
    )


def _source_values_with_payload(
    payload_update: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    values = _JSON_OBJECT.validate_python(_source_record().model_dump())
    payload = _JSON_OBJECT.validate_python(values["payload"])
    payload.update(payload_update)
    values["payload"] = payload
    values["payload_sha256"] = hashlib.sha256(
        canonical_json_value_bytes(payload)
    ).hexdigest()
    return values


def _validate_source_values(
    boundary: Literal["python", "json"], values: dict[str, JsonValue]
) -> BrightDataLinkedInPostSourceRecord:
    if boundary == "python":
        return BrightDataLinkedInPostSourceRecord.model_validate(values)
    return BrightDataLinkedInPostSourceRecord.model_validate_json(
        canonical_json_value_bytes(values)
    )


def test_source_record_round_trip_preserves_schema_v2_url_owner() -> None:
    # Given
    source = _source_record()

    # When
    restored = BrightDataLinkedInPostSourceRecord.model_validate_json(
        source.model_dump_json()
    )

    # Then
    assert restored == source
    assert restored.schema_version == 2
    assert restored.account_id == _ACCOUNT_URL
    assert (
        source_record_path(restored)
        .as_posix()
        .startswith("source/brightdata/linkedin/posts/")
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("account_id", "linkedin:person:12345"),
        ("account_id", 12345),
        ("account_id", "https://linkedin.com/in/synthetic-ada/"),
        ("account_id", "https://www.linkedin.com/in/synthetic-ada"),
    ],
)
def test_source_record_rejects_v1_or_noncanonical_owner(
    field: str, value: str | int
) -> None:
    # Given
    values = _source_record().model_dump()
    values[field] = value

    # When / Then
    with pytest.raises(ValidationError):
        _ = BrightDataLinkedInPostSourceRecord.model_validate(values)


@pytest.mark.parametrize("field", ["schema_version", "provider", "dataset_id"])
def test_source_record_requires_explicit_version_and_provenance(field: str) -> None:
    # Given
    values = _source_record().model_dump()
    del values[field]

    # When / Then
    with pytest.raises(ValidationError) as captured:
        _ = BrightDataLinkedInPostSourceRecord.model_validate(values)
    assert captured.value.errors(include_input=False)[0]["loc"] == (field,)


@pytest.mark.parametrize("boundary", ["python", "json"])
@pytest.mark.parametrize(
    "payload_update",
    [
        {"snapshot_id": _CANARY},
        {"snapshotId": _CANARY},
        {"API Key": _CANARY},
        {"authorization": _CANARY},
        {"provider_metadata": {"snapshot_id": _CANARY}},
        {"provider": {"request": {"headers": {"Authorization": _CANARY}}}},
        {"provider": {"requestHeader": {"x-synthetic-header": _CANARY}}},
        {"provider": {"request_header": {"x-synthetic-header": _CANARY}}},
        {"provider": {"request-header": {"x-synthetic-header": _CANARY}}},
        {"provider": {"requestHeaders": {"x-synthetic-header": _CANARY}}},
        {"provider": {"request_headers": {"x-synthetic-header": _CANARY}}},
        {"provider": {"request-headers": {"x-synthetic-header": _CANARY}}},
        {"provider": {"responseHeader": {"x-synthetic-header": _CANARY}}},
        {"provider": {"response_header": {"x-synthetic-header": _CANARY}}},
        {"provider": {"response-header": {"x-synthetic-header": _CANARY}}},
        {"provider": {"responseHeaders": {"x-synthetic-header": _CANARY}}},
        {"provider": {"response_headers": {"x-synthetic-header": _CANARY}}},
        {"provider": {"response-headers": {"x-synthetic-header": _CANARY}}},
        {"provider": {"requestAuth": {"value": _CANARY}}},
        {"provider": {"REQUEST_AUTH": {"value": _CANARY}}},
        {"provider": {"request-auth": {"value": _CANARY}}},
        {"provider": {"requestCookie": {"value": _CANARY}}},
        {"provider": {"REQUEST_COOKIE": {"value": _CANARY}}},
        {"provider": {"request-cookie": {"value": _CANARY}}},
        {"provider": {"requestCredentials": {"value": _CANARY}}},
        {"provider": {"REQUEST_CREDENTIALS": {"value": _CANARY}}},
        {"provider": {"request-credentials": {"value": _CANARY}}},
        {"provider": {"clientRequestHeaders": {"value": _CANARY}}},
        {"provider": {"CLIENT_REQUEST_HEADERS": {"value": _CANARY}}},
        {"provider": {"client-request-headers": {"value": _CANARY}}},
        {"provider": {"providerAuthInfo": {"value": _CANARY}}},
        {"provider": {"PROVIDER_AUTH_INFO": {"value": _CANARY}}},
        {"provider": {"provider-auth-info": {"value": _CANARY}}},
        {"provider": {"responseAuthentication": {"value": _CANARY}}},
        {"provider": {"RESPONSE_AUTHENTICATION": {"value": _CANARY}}},
        {"provider": {"response-authentication": {"value": _CANARY}}},
        {"context": [{"client_secret": _CANARY}]},
    ],
)
def test_source_record_rejects_rehashed_sensitive_payload_without_canary_leak(
    boundary: Literal["python", "json"],
    payload_update: dict[str, JsonValue],
) -> None:
    # Given
    values = _source_values_with_payload(payload_update)

    # When / Then
    with pytest.raises(ValidationError) as captured:
        _ = _validate_source_values(boundary, values)
    diagnostic = f"{captured.value!s} {captured.value!r}"
    assert _CANARY not in diagnostic
    assert all(marker not in diagnostic for marker in _SENSITIVE_MARKERS)


@pytest.mark.parametrize("boundary", ["python", "json"])
def test_source_record_preserves_ordinary_nested_provider_content(
    boundary: Literal["python", "json"],
) -> None:
    # Given
    ordinary: dict[str, JsonValue] = {
        "provider_details": {
            "campaign": {"label": "Synthetic launch", "impressions": 42},
            "annotations": ["featured", {"locale": "en-US"}],
        }
    }
    values = _source_values_with_payload(ordinary)

    # When
    restored = _validate_source_values(boundary, values)

    # Then
    assert restored.payload["provider_details"] == ordinary["provider_details"]
