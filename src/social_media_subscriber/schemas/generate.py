"""Generate deterministic committed JSON Schema contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import BaseModel, TypeAdapter

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import (
    LINKEDIN_ACCOUNT_ID_PATTERN,
    LINKEDIN_COMPANY_ACCOUNT_ID_PATTERN,
    LINKEDIN_PERSON_ACCOUNT_ID_PATTERN,
    X_ACCOUNT_ID_PATTERN,
)
from social_media_subscriber.domain.post import Post
from social_media_subscriber.domain.post_index import (
    LINKEDIN_POST_INDEX_PATH_PATTERN,
    X_POST_INDEX_PATH_PATTERN,
    PostsIndex,
)
from social_media_subscriber.platforms.linkedin import LINKEDIN_POST_URL_PATTERN
from social_media_subscriber.platforms.x import (
    X_PLATFORM_POST_ID_PATTERN,
    X_POST_URL_PATTERN,
)
from social_media_subscriber.serialization.json import (
    JsonValue,
    canonical_json_value_bytes,
)

_SCHEMA_MODELS: Final[tuple[tuple[str, type[BaseModel]], ...]] = (
    ("account.schema.json", Account),
    ("post.schema.json", Post),
    ("posts-index.schema.json", PostsIndex),
)
_JSON_OBJECT_ADAPTER: Final[TypeAdapter[dict[str, JsonValue]]] = TypeAdapter(
    dict[str, JsonValue]
)
_DEFAULT_SCHEMA_DIRECTORY: Final = Path("schemas")
_SCHEMA_ONE_OF_BY_FILENAME: Final[dict[str, tuple[dict[str, JsonValue], ...]]] = {
    "account.schema.json": (
        {
            "properties": {
                "platform": {"const": "linkedin"},
                "kind": {"const": "person"},
                "profile_url": {"pattern": LINKEDIN_PERSON_ACCOUNT_ID_PATTERN},
            }
        },
        {
            "properties": {
                "platform": {"const": "linkedin"},
                "kind": {"const": "company"},
                "profile_url": {"pattern": LINKEDIN_COMPANY_ACCOUNT_ID_PATTERN},
            }
        },
        {
            "properties": {
                "platform": {"const": "x"},
                "kind": {"const": "profile"},
                "profile_url": {"pattern": X_ACCOUNT_ID_PATTERN},
            }
        },
    ),
    "post.schema.json": (
        {
            "properties": {
                "account_profile_url": {"pattern": LINKEDIN_ACCOUNT_ID_PATTERN},
                "canonical_url": {"pattern": LINKEDIN_POST_URL_PATTERN},
            }
        },
        {
            "properties": {
                "account_profile_url": {"pattern": X_ACCOUNT_ID_PATTERN},
                "canonical_url": {"pattern": X_POST_URL_PATTERN},
                "platform_post_id": {"pattern": X_PLATFORM_POST_ID_PATTERN},
            }
        },
    ),
}
_POST_INDEX_ENTRY_ONE_OF: Final[tuple[dict[str, JsonValue], ...]] = (
    {
        "properties": {
            "path": {"pattern": LINKEDIN_POST_INDEX_PATH_PATTERN},
            "account_profile_url": {"pattern": LINKEDIN_ACCOUNT_ID_PATTERN},
            "platform": {"const": "linkedin"},
        }
    },
    {
        "properties": {
            "path": {"pattern": X_POST_INDEX_PATH_PATTERN},
            "account_profile_url": {"pattern": X_ACCOUNT_ID_PATTERN},
            "platform": {"const": "x"},
        }
    },
)


def generate_schemas(output_directory: Path | None = None) -> tuple[Path, ...]:
    """Generate all public schemas in stable filename order."""
    destination = output_directory or _DEFAULT_SCHEMA_DIRECTORY
    generated: list[tuple[Path, bytes]] = []
    for filename, model in _SCHEMA_MODELS:
        schema = _JSON_OBJECT_ADAPTER.validate_python(model.model_json_schema())
        branches = _SCHEMA_ONE_OF_BY_FILENAME.get(filename)
        if branches is not None:
            schema["oneOf"] = list(branches)
        if filename == "posts-index.schema.json":
            definitions = _JSON_OBJECT_ADAPTER.validate_python(schema["$defs"])
            entry = _JSON_OBJECT_ADAPTER.validate_python(definitions["PostIndexEntry"])
            entry["oneOf"] = list(_POST_INDEX_ENTRY_ONE_OF)
            definitions["PostIndexEntry"] = entry
            schema["$defs"] = definitions
        generated.append((destination / filename, canonical_json_value_bytes(schema)))
    schemas = tuple(generated)
    destination.mkdir(parents=True, exist_ok=True)
    for path, payload in schemas:
        _ = path.write_bytes(payload)
    return tuple(path for path, _payload in schemas)


def main() -> None:
    """Generate schemas into the repository contract directory."""
    _ = generate_schemas()


if __name__ == "__main__":
    main()
