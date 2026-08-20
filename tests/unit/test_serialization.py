from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import TypeAdapter, ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import PlatformAccountId, account_id_for
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


def _account(aliases: tuple[str, ...]) -> Account:
    platform_account_id = PlatformAccountId("12345")
    return Account(
        id=account_id_for(AccountKind.PERSON, platform_account_id),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        platform_account_id=platform_account_id,
        profile_url="https://www.linkedin.com/in/ada/",
        url_aliases=aliases,
        first_seen_at=datetime(2026, 8, 20, 12, 30, tzinfo=UTC),
    )


def test_canonical_json_is_utf8_sorted_indented_lf_terminated_and_deterministic() -> (
    None
):
    # Given
    ordered = _account(
        (
            "https://www.linkedin.com/in/ada/",
            "https://www.linkedin.com/in/ada-lovelace/",
        )
    )
    shuffled = _account(tuple(reversed(ordered.url_aliases)))
    shuffled_fields = Account.model_validate(
        dict(reversed(tuple(ordered.model_dump().items())))
    )

    # When
    first = canonical_json_bytes(ordered)
    second = canonical_json_bytes(shuffled)
    third = canonical_json_bytes(shuffled_fields)

    # Then
    assert first == second
    assert first == third
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")
    assert b"\r" not in first
    assert first.decode("utf-8").startswith('{\n  "first_seen_at"')
    assert json.loads(first) == ordered.model_dump(mode="json")


def test_write_and_read_json_round_trip_typed_equality(tmp_path: Path) -> None:
    # Given
    account = _account(("https://www.linkedin.com/in/ada/",))
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
    invalid = canonical_json_bytes(
        _account(("https://www.linkedin.com/in/ada/",))
    ).replace(b"linkedin:person:12345", b"linkedin:company:12345")
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
        match _JSON_ADAPTER.validate_json(path.read_bytes()):
            case {
                "additionalProperties": False,
                "properties": {"schema_version": {"const": 1}},
            }:
                pass
            case unexpected:
                pytest.fail(f"unexpected schema structure: {unexpected!r}")

    account_schema = _JSON_ADAPTER.validate_json(first_paths[0].read_bytes())
    match account_schema:
        case {
            "properties": {
                "id": {"pattern": str() as account_id_pattern},
                "platform_account_id": {"pattern": str() as platform_id_pattern},
                "profile_url": {"pattern": str() as profile_pattern},
                "url_aliases": {"items": {"pattern": str() as alias_pattern}},
            }
        }:
            assert account_id_pattern == r"^linkedin:(?:person|company):[0-9]+$"
            assert platform_id_pattern == r"^[0-9]+$"
            for pattern in (profile_pattern, alias_pattern):
                assert re.fullmatch(
                    pattern,
                    "https://www.linkedin.com/in/synthetic/",
                )
                for unsafe_url in (
                    "https://www.linkedin.com/in/syn\nthetic/",
                    "https://www.linkedin.com/in/syn%2Fthetic/",
                    "https://www.linkedin.com/in/syn%5cthetic/",
                    "https://www.linkedin.com/in/%2e%2E/",
                    "https://www.linkedin.com/in/../",
                ):
                    assert re.fullmatch(pattern, unsafe_url) is None
        case unexpected:
            pytest.fail(f"unexpected Account ID schema: {unexpected!r}")

    post_schema = _JSON_ADAPTER.validate_json(first_paths[1].read_bytes())
    match post_schema:
        case {
            "properties": {"account_id": {"pattern": str() as post_account_id_pattern}}
        }:
            for valid_id in (
                "linkedin:person:0",
                "linkedin:person:123",
                "linkedin:company:456",
            ):
                assert re.fullmatch(post_account_id_pattern, valid_id)
            for invalid_id in (
                "",
                "linkedin:person:abc",
                "linkedin:company:../../x",
                "linkedin:person:\uff11\uff12\uff13",
                "linkedin:person:123/456",
                "urn:li:person:123",
            ):
                assert re.fullmatch(post_account_id_pattern, invalid_id) is None
        case unexpected:
            pytest.fail(f"unexpected Post AccountId schema: {unexpected!r}")
