"""Canonical JSON encoding for persisted boundary models."""

from __future__ import annotations

import json
from typing import Final, Protocol

from pydantic import TypeAdapter

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)

_JSON_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


class JsonBoundaryModel(Protocol):
    """Pydantic-compatible model surface needed by canonical serialization."""

    def model_dump_json(self) -> str:
        """Return the validated model's JSON representation."""
        ...


def canonical_json_value_bytes(value: JsonValue) -> bytes:
    """Encode one JSON value with stable key and whitespace rules."""
    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return f"{text}\n".encode()


def canonical_json_bytes(model: JsonBoundaryModel) -> bytes:
    """Encode one validated model as deterministic canonical UTF-8 JSON."""
    value = _JSON_ADAPTER.validate_json(model.model_dump_json())
    return canonical_json_value_bytes(value)
