"""Canonical JSON encoding and atomic typed model persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final, Protocol

from pydantic import BaseModel, TypeAdapter

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


def write_json(destination: Path, model: JsonBoundaryModel) -> None:
    """Atomically write a validated model without exposing partial content."""
    payload = canonical_json_bytes(model)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            _ = temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        _ = temporary_path.replace(destination)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def read_json[ModelT: BaseModel](source: Path, model_type: type[ModelT]) -> ModelT:
    """Read and validate one canonical model from disk."""
    return model_type.model_validate_json(source.read_bytes())
