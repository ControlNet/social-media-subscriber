from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import TypeAdapter, ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.schemas.generate import generate_schemas
from social_media_subscriber.serialization.json import (
    JsonValue,
    canonical_json_bytes,
    read_json,
    write_json,
)

if TYPE_CHECKING:
    from pathlib import Path

_JSON_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_REQUIRED_FIELDS_ADAPTER: Final[TypeAdapter[list[str]]] = TypeAdapter(list[str])


def _required_fields(schema: JsonValue) -> set[str]:
    assert isinstance(schema, dict)
    return set(_REQUIRED_FIELDS_ADAPTER.validate_python(schema.get("required")))


def _account() -> Account:
    profile_url = "https://www.linkedin.com/in/synthetic-ada/"
    return Account(
        schema_version=2,
        id=AccountId(profile_url),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=profile_url,
        first_seen_at=datetime(2026, 8, 20, 12, 30, tzinfo=UTC),
    )


def test_canonical_json_is_utf8_sorted_indented_lf_terminated_and_deterministic() -> (
    None
):
    # Given
    ordered = _account()
    shuffled_fields = Account.model_validate(
        dict(reversed(tuple(ordered.model_dump().items())))
    )

    # When
    first = canonical_json_bytes(ordered)
    second = canonical_json_bytes(shuffled_fields)

    # Then
    assert first == second
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")
    assert b"\r" not in first
    assert first.decode("utf-8").startswith('{\n  "first_seen_at"')
    assert json.loads(first) == ordered.model_dump(mode="json")


def test_write_and_read_json_round_trip_typed_equality(tmp_path: Path) -> None:
    # Given
    account = _account()
    destination = tmp_path / "account.json"

    # When
    write_json(destination, account)
    reloaded = read_json(destination, Account)

    # Then
    assert reloaded == account
    assert destination.read_bytes() == canonical_json_bytes(account)


def test_invalid_model_does_not_create_partial_file(tmp_path: Path) -> None:
    # Given
    destination = tmp_path / "account.json"
    invalid = canonical_json_bytes(_account()).replace(
        b'"schema_version": 2', b'"schema_version": 1'
    )
    _ = destination.write_bytes(invalid)

    # When / Then
    with pytest.raises(ValidationError):
        _ = read_json(destination, Account)
    assert tuple(tmp_path.iterdir()) == (destination,)


def test_schema_generation_is_deterministic_and_structural(tmp_path: Path) -> None:
    # Given / When
    first_paths = generate_schemas(tmp_path)
    first_bytes = tuple(path.read_bytes() for path in first_paths)
    second_paths = generate_schemas(tmp_path)

    # Then
    assert tuple(path.name for path in first_paths) == (
        "account.schema.json",
        "brightdata-linkedin-post.schema.json",
        "post.schema.json",
    )
    assert tuple(path.read_bytes() for path in second_paths) == first_bytes
    for path in second_paths:
        schema = _JSON_ADAPTER.validate_json(path.read_bytes())
        match schema:
            case {
                "additionalProperties": False,
                "properties": {"schema_version": {"const": 2}},
            }:
                pass
            case unexpected:
                pytest.fail(f"unexpected schema structure: {unexpected!r}")
        assert "schema_version" in _required_fields(schema)

    account_schema = _JSON_ADAPTER.validate_json(first_paths[0].read_bytes())
    match account_schema:
        case {
            "properties": {
                "id": {"pattern": str() as account_id_pattern},
                "profile_url": {"pattern": str() as profile_pattern},
            }
        }:
            assert account_id_pattern == profile_pattern
            for pattern in (account_id_pattern, profile_pattern):
                assert re.fullmatch(
                    pattern,
                    "https://www.linkedin.com/in/synthetic/",
                )
                for unsafe_url in (
                    "https://www.linkedin.com/in/syn\nthetic/",
                    "https://www.linkedin.com/in/synthetic?tracking=unsafe/",
                    "https://www.linkedin.com/in/synthetic#about/",
                    "https://www.linkedin.com/in/syn%2Fthetic/",
                    "https://www.linkedin.com/in/syn%5cthetic/",
                    "https://www.linkedin.com/in/%2e%2E/",
                    "https://www.linkedin.com/in/synthetic%ZZ/",
                    "https://www.linkedin.com/in/synthetic%FF/",
                    "https://www.linkedin.com/in/synthetic%F0%28%8C%28/",
                    "https://www.linkedin.com/in/synthetic%E9%9B%AA/",
                    "https://www.linkedin.com/in/../",
                ):
                    assert re.fullmatch(pattern, unsafe_url) is None
        case unexpected:
            pytest.fail(f"unexpected Account ID schema: {unexpected!r}")

    for schema_path in first_paths[1:]:
        schema = _JSON_ADAPTER.validate_json(schema_path.read_bytes())
        match schema:
            case {
                "properties": {
                    "account_id": {"pattern": str() as post_account_id_pattern}
                }
            }:
                pass
            case unexpected:
                pytest.fail(f"unexpected owned AccountId schema: {unexpected!r}")
        for valid_id in (
            "https://www.linkedin.com/in/synthetic-ada/",
            "https://www.linkedin.com/company/synthetic-labs/",
        ):
            assert re.fullmatch(post_account_id_pattern, valid_id)
        for invalid_id in (
            "linkedin:person:123",
            "https://linkedin.com/in/synthetic-ada/",
            "https://www.linkedin.com/in/synthetic-ada",
            "https://www.linkedin.com/in/synthetic-ada?tracking=unsafe/",
            "https://www.linkedin.com/in/synthetic-ada#about/",
            "https://www.linkedin.com/in/synthetic%2eada/",
            "https://www.linkedin.com/in/synthetic%ZZ/",
            "https://www.linkedin.com/in/synthetic%FF/",
            "https://www.linkedin.com/in/synthetic%F0%28%8C%28/",
            "https://www.linkedin.com/company/synthetic%E9%9B%AA/",
        ):
            assert re.fullmatch(post_account_id_pattern, invalid_id) is None

    source_schema = _JSON_ADAPTER.validate_json(first_paths[1].read_bytes())
    assert {"schema_version", "provider", "dataset_id"} <= _required_fields(
        source_schema
    )
