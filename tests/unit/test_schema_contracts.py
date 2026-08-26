from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import TypeAdapter

from social_media_subscriber.schemas.generate import generate_schemas
from social_media_subscriber.serialization.json import JsonValue

if TYPE_CHECKING:
    from pathlib import Path

type JsonObject = dict[str, JsonValue]
type GeneratedSchemas = tuple[JsonObject, JsonObject, JsonObject]

_JSON_OBJECT_ADAPTER: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)
_JSON_ARRAY_ADAPTER: Final[TypeAdapter[list[JsonValue]]] = TypeAdapter(list[JsonValue])
_STRING_ADAPTER: Final[TypeAdapter[str]] = TypeAdapter(str)
_STRING_LIST_ADAPTER: Final[TypeAdapter[list[str]]] = TypeAdapter(list[str])


def _object(value: JsonValue) -> JsonObject:
    return _JSON_OBJECT_ADAPTER.validate_python(value)


def _array(value: JsonValue) -> list[JsonValue]:
    return _JSON_ARRAY_ADAPTER.validate_python(value)


def _properties(schema: JsonObject) -> JsonObject:
    return _object(schema["properties"])


def _branches(schema: JsonObject) -> list[JsonValue]:
    return _array(schema["oneOf"])


def _branch_properties(schema: JsonObject, index: int) -> JsonObject:
    return _properties(_object(_branches(schema)[index]))


def _property(properties: JsonObject, name: str) -> JsonObject:
    return _object(properties[name])


def _pattern(properties: JsonObject, name: str) -> str:
    return _STRING_ADAPTER.validate_python(_property(properties, name)["pattern"])


def _const(properties: JsonObject, name: str) -> str:
    return _STRING_ADAPTER.validate_python(_property(properties, name)["const"])


def _required_fields(schema: JsonObject) -> set[str]:
    return set(_STRING_LIST_ADAPTER.validate_python(schema.get("required")))


def _assert_pattern_rejects(pattern: str, values: tuple[str, ...]) -> None:
    assert not any(re.fullmatch(pattern, value) for value in values)


@pytest.fixture
def generated_schemas(tmp_path: Path) -> GeneratedSchemas:
    first_paths = generate_schemas(tmp_path)
    first_bytes = tuple(path.read_bytes() for path in first_paths)
    second_paths = generate_schemas(tmp_path)

    assert tuple(path.name for path in first_paths) == (
        "account.schema.json",
        "post.schema.json",
        "posts-index.schema.json",
    )
    assert tuple(path.read_bytes() for path in second_paths) == first_bytes
    schemas = tuple(
        _JSON_OBJECT_ADAPTER.validate_json(path.read_bytes()) for path in second_paths
    )
    assert len(schemas) == 3
    for schema in schemas:
        assert schema["additionalProperties"] is False
        assert "schema_version" not in _required_fields(schema)
    return schemas[0], schemas[1], schemas[2]


def test_account_schema_couples_platform_kind_and_identity(
    generated_schemas: GeneratedSchemas,
) -> None:
    account_schema, _, _ = generated_schemas
    assert len(_branches(account_schema)) == 3
    person = _branch_properties(account_schema, 0)
    company = _branch_properties(account_schema, 1)
    x_profile = _branch_properties(account_schema, 2)
    properties = _properties(account_schema)

    assert _const(person, "platform") == "linkedin"
    assert _const(person, "kind") == "person"
    assert _const(company, "platform") == "linkedin"
    assert _const(company, "kind") == "company"
    assert _const(x_profile, "platform") == "x"
    assert _const(x_profile, "kind") == "profile"

    person_pattern = _pattern(person, "profile_url")
    company_pattern = _pattern(company, "profile_url")
    x_pattern = _pattern(x_profile, "profile_url")
    profile_pattern = _pattern(properties, "profile_url")
    assert re.fullmatch(person_pattern, "https://www.linkedin.com/in/synthetic/")
    assert re.fullmatch(
        company_pattern,
        "https://www.linkedin.com/company/synthetic/",
    )
    assert re.fullmatch(x_pattern, "https://x.com/synthetic_user/")
    assert re.fullmatch(person_pattern, "https://x.com/synthetic_user/") is None
    assert (
        re.fullmatch(company_pattern, "https://www.linkedin.com/in/synthetic/") is None
    )
    assert re.fullmatch(profile_pattern, "https://www.linkedin.com/in/synthetic/")
    _assert_pattern_rejects(
        profile_pattern,
        (
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
            "https://x.com/login/",
            "https://x.com/signup/",
        ),
    )


def test_post_schema_couples_owner_url_and_post_identity(
    generated_schemas: GeneratedSchemas,
) -> None:
    _, post_schema, _ = generated_schemas
    assert len(_branches(post_schema)) == 2
    linkedin = _branch_properties(post_schema, 0)
    x_post = _branch_properties(post_schema, 1)
    properties = _properties(post_schema)

    linkedin_owner_pattern = _pattern(linkedin, "account_profile_url")
    linkedin_post_pattern = _pattern(linkedin, "canonical_url")
    x_owner_pattern = _pattern(x_post, "account_profile_url")
    x_post_pattern = _pattern(x_post, "canonical_url")
    x_post_id_pattern = _pattern(x_post, "platform_post_id")
    owner_pattern = _pattern(properties, "account_profile_url")

    assert re.fullmatch(
        linkedin_owner_pattern,
        "https://www.linkedin.com/in/synthetic-ada/",
    )
    assert re.fullmatch(
        linkedin_post_pattern,
        "https://www.linkedin.com/feed/update/urn:li:activity:123/",
    )
    assert re.fullmatch(x_owner_pattern, "https://x.com/synthetic_user/")
    assert re.fullmatch(x_post_pattern, "https://x.com/synthetic_user/status/123")
    assert re.fullmatch(x_post_id_pattern, "123")
    _assert_pattern_rejects(x_post_id_pattern, ("0", "01", "not-numeric"))
    _assert_pattern_rejects(
        linkedin_post_pattern,
        (
            "https://www.linkedin.com/posts/../synthetic",
            "https://www.linkedin.com/feed/update/./synthetic",
            "https://x.com/user/status/123",
        ),
    )
    _assert_pattern_rejects(
        x_post_pattern,
        (
            "https://x.com/login/status/123",
            "https://x.com/signup/status/123",
            "https://www.linkedin.com/posts/synthetic-activity/",
        ),
    )
    for valid_owner in (
        "https://www.linkedin.com/in/synthetic-ada/",
        "https://www.linkedin.com/company/synthetic-labs/",
        "https://x.com/synthetic_user/",
    ):
        assert re.fullmatch(owner_pattern, valid_owner)
    _assert_pattern_rejects(
        owner_pattern,
        (
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
            "https://x.com/login/",
            "https://x.com/signup/",
            "https://x.com/too-long-synthetic-user/",
        ),
    )


def test_posts_index_schema_couples_platform_owner_and_path(
    generated_schemas: GeneratedSchemas,
) -> None:
    _, _, posts_index_schema = generated_schemas
    definitions = _object(posts_index_schema["$defs"])
    entry = _object(definitions["PostIndexEntry"])
    assert len(_branches(entry)) == 2
    linkedin = _branch_properties(entry, 0)
    x_post = _branch_properties(entry, 1)

    assert _const(linkedin, "platform") == "linkedin"
    assert _const(x_post, "platform") == "x"
    linkedin_path_pattern = _pattern(linkedin, "path")
    x_path_pattern = _pattern(x_post, "path")
    linkedin_owner_pattern = _pattern(linkedin, "account_profile_url")
    x_owner_pattern = _pattern(x_post, "account_profile_url")
    assert re.fullmatch(
        linkedin_path_pattern,
        "posts/linkedin/" + "a" * 64 + ".json",
    )
    assert re.fullmatch(x_path_pattern, "posts/x/" + "b" * 64 + ".json")
    assert re.fullmatch(linkedin_owner_pattern, "https://x.com/user/") is None
    assert (
        re.fullmatch(
            x_owner_pattern,
            "https://www.linkedin.com/in/synthetic/",
        )
        is None
    )
