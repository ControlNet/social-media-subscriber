"""Run explicitly synthetic URL identity scenarios through the real CLI stack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from tests.e2e.pipeline_harness import (
    ContainedScenario,
    invoke_contained_scenario,
    report,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Emit the real CLI's one-line report for a task-owned loopback run."""
    parser = argparse.ArgumentParser()
    _ = parser.add_argument(
        "--scenario",
        choices=("success", "not-found"),
        required=True,
    )
    _ = parser.add_argument("--root", type=str, required=True)
    args = parser.parse_args(argv)
    scenario = cast("ContainedScenario", args.scenario)
    root = Path(cast("str", args.root))
    result = invoke_contained_scenario(scenario, root)
    line = json.dumps(report(result), sort_keys=True, separators=(",", ":"))
    _ = sys.stdout.write(f"{line}\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
