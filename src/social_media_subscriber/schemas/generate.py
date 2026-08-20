"""Generate deterministic committed JSON Schema contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import BaseModel, TypeAdapter

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.post import Post
from social_media_subscriber.serialization.json import (
    JsonValue,
    canonical_json_value_bytes,
)

_SCHEMA_MODELS: Final[tuple[tuple[str, type[BaseModel]], ...]] = (
    ("account.schema.json", Account),
    ("post.schema.json", Post),
)
_JSON_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_DEFAULT_SCHEMA_DIRECTORY: Final = Path("schemas")


def generate_schemas(output_directory: Path | None = None) -> tuple[Path, ...]:
    """Generate all public schemas in stable filename order."""
    destination = output_directory or _DEFAULT_SCHEMA_DIRECTORY
    schemas = tuple(
        (
            destination / filename,
            canonical_json_value_bytes(
                _JSON_ADAPTER.validate_python(model.model_json_schema())
            ),
        )
        for filename, model in _SCHEMA_MODELS
    )
    destination.mkdir(parents=True, exist_ok=True)
    for path, payload in schemas:
        _ = path.write_bytes(payload)
    return tuple(path for path, _payload in schemas)


def main() -> None:
    """Generate schemas into the repository contract directory."""
    _ = generate_schemas()


if __name__ == "__main__":
    main()
