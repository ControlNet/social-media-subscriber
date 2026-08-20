from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from pathlib import Path

type YamlValue = (
    str | bool | int | float | list[YamlValue] | dict[str, YamlValue] | None
)

_WORKFLOW_ADAPTER: Final = TypeAdapter(dict[str, YamlValue])
_SAFE_YAML_TO_JSON: Final = (
    "import json, pathlib, sys, yaml; "
    "print(json.dumps(yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())))"
)


def load_workflow(path: Path) -> dict[str, YamlValue]:
    completed = subprocess.run(  # noqa: S603 - current interpreter is trusted
        (sys.executable, "-c", _SAFE_YAML_TO_JSON, str(path)),
        check=True,
        capture_output=True,
        text=True,
    )
    return _WORKFLOW_ADAPTER.validate_json(completed.stdout)


def mapping(value: YamlValue) -> dict[str, YamlValue]:
    assert isinstance(value, dict)
    return value


def sequence(value: YamlValue) -> list[YamlValue]:
    assert isinstance(value, list)
    return value


def text(value: YamlValue) -> str:
    assert isinstance(value, str)
    return value
