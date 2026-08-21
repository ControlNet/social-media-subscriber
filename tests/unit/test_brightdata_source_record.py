from __future__ import annotations

from typing import Final

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.providers.brightdata.models import BrightDataPost
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
    source_record_path,
)

_ACCOUNT_URL: Final = "https://www.linkedin.com/in/synthetic-ada/"


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
