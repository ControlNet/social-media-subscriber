from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from tests.workflow_helpers import YamlValue, load_workflow, mapping, sequence, text

_ROOT: Final = Path(__file__).parents[2]
_CI_PATH: Final = _ROOT / ".github" / "workflows" / "ci.yml"
_COLLECT_PATH: Final = _ROOT / ".github" / "workflows" / "collect.yml"
_CHECKOUT: Final = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
_SETUP_PIXI: Final = "prefix-dev/setup-pixi@f00437f565399d418b0acc85936d12c1fb668347"
_BASH: Final = shutil.which("bash")


def _steps(workflow: dict[str, YamlValue], job_name: str) -> list[dict[str, YamlValue]]:
    jobs = mapping(workflow["jobs"])
    job = mapping(jobs[job_name])
    return [mapping(step) for step in sequence(job["steps"])]


def _run_preflight(
    tmp_path: Path,
    event_name: str,
    *,
    has_accounts: bool,
    has_keys: bool,
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    workflow = load_workflow(_COLLECT_PATH)
    command = text(_steps(workflow, "preflight")[0]["run"]).replace(
        "${{ github.event_name }}", event_name
    )
    output = tmp_path / "github-output"
    return subprocess.run(  # noqa: S603 - resolved Bash executes owned workflow text
        (_BASH, "-c", command),
        check=False,
        capture_output=True,
        text=True,
        env={
            "GITHUB_OUTPUT": str(output),
            "HAS_ACCOUNTS": str(has_accounts).lower(),
            "HAS_BRIGHT_DATA_API_KEYS": str(has_keys).lower(),
        },
    )


def test_ci_is_read_only_immutable_and_secret_free() -> None:
    # Given / When
    workflow = load_workflow(_CI_PATH)
    source = _CI_PATH.read_text()

    # Then
    assert mapping(workflow["on"]) == {"push": None, "pull_request": None}
    assert mapping(workflow["permissions"]) == {"contents": "read"}
    steps = _steps(workflow, "verify")
    assert [step.get("uses") for step in steps if "uses" in step] == [
        _CHECKOUT,
        _SETUP_PIXI,
    ]
    setup = next(step for step in steps if step.get("uses") == _SETUP_PIXI)
    assert mapping(setup["with"])["locked"] is True
    commands = "\n".join(text(step["run"]) for step in steps if "run" in step)
    assert "pixi install --locked" in commands
    assert "pixi run verify" in commands
    assert "secrets." not in source
    assert "BRIGHT_DATA" not in source
    assert "ACCOUNTS" not in source


def test_collection_has_exact_triggers_inputs_and_non_cancelling_lock() -> None:
    # Given / When
    workflow = load_workflow(_COLLECT_PATH)
    triggers = mapping(workflow["on"])

    # Then
    schedule = mapping(sequence(triggers["schedule"])[0])
    assert schedule == {"cron": "17 3 * * *"}
    dispatch = mapping(triggers["workflow_dispatch"])
    inputs = mapping(dispatch["inputs"])
    assert set(inputs) == {"start_date", "end_date"}
    for name in ("start_date", "end_date"):
        item = mapping(inputs[name])
        assert item["required"] is False
        assert item["type"] == "string"
    assert mapping(workflow["concurrency"]) == {
        "group": "social-media-subscriber-dist",
        "cancel-in-progress": False,
    }
    assert mapping(workflow["permissions"]) == {"contents": "read"}


def test_collection_gates_secrets_and_scopes_write_to_publication_job() -> None:
    # Given / When
    workflow = load_workflow(_COLLECT_PATH)
    jobs = mapping(workflow["jobs"])
    preflight = mapping(jobs["preflight"])
    publication = mapping(jobs["publication"])
    preflight_source = "\n".join(
        text(step["run"]) for step in _steps(workflow, "preflight") if "run" in step
    )

    # Then
    assert mapping(preflight["permissions"]) == {"contents": "read"}
    assert mapping(publication["permissions"]) == {"contents": "write"}
    assert publication["if"] == "needs.preflight.outputs.enabled == 'true'"
    assert preflight["timeout-minutes"] == 5
    assert publication["timeout-minutes"] == 45
    assert mapping(publication["env"])["GITHUB_TOKEN"] == "${{ github." + "token }}"
    assert '${{ github.event_name }}" == "workflow_dispatch' in preflight_source
    assert "exit 1" in preflight_source
    assert "enabled=false" in preflight_source
    assert "secrets.ACCOUNTS" in _COLLECT_PATH.read_text()
    assert "secrets.BRIGHT_DATA_API_KEYS" in _COLLECT_PATH.read_text()
    assert "pull_request" not in mapping(workflow["on"])


@pytest.mark.parametrize(
    "scenario",
    [
        ("schedule", False, False, 0, "enabled=false\n"),
        ("workflow_dispatch", False, False, 1, None),
        ("workflow_dispatch", True, True, 0, "enabled=true\n"),
    ],
)
def test_preflight_event_outcomes(
    tmp_path: Path,
    scenario: tuple[str, bool, bool, int, str | None],
) -> None:
    # Given / When
    event_name, has_accounts, has_keys, exit_code, output = scenario
    result = _run_preflight(
        tmp_path,
        event_name,
        has_accounts=has_accounts,
        has_keys=has_keys,
    )
    output_path = tmp_path / "github-output"
    actual_output = output_path.read_text() if output_path.exists() else None

    # Then
    assert result.returncode == exit_code
    assert actual_output == output


def test_collection_preserves_observed_lease_and_terminal_status_contract() -> None:
    # Given / When
    workflow = load_workflow(_COLLECT_PATH)
    commands = "\n".join(
        text(step["run"]) for step in _steps(workflow, "publication") if "run" in step
    )

    # Then
    assert commands.count("ls-remote --heads origin refs/heads/dist") == 1
    assert "checkout-index" in commands
    assert "--previous-snapshot" in commands
    assert '--expected-sha "$EXPECTED_SHA"' in commands
    assert "verify-snapshot" in commands
    assert re.search(r"COLLECT_STATUS.*(?:0|4)", commands, re.DOTALL)
    assert 'if [[ "$COLLECT_STATUS" -eq 4 ]]' in commands
    assert "exit 4" in commands


@pytest.mark.parametrize("path", [_CI_PATH, _COLLECT_PATH])
def test_workflow_uses_are_full_shas_and_unsafe_publish_patterns_are_absent(
    path: Path,
) -> None:
    # Given / When
    source = path.read_text()
    workflow = load_workflow(path)
    all_steps = [
        step
        for job in mapping(workflow["jobs"]).values()
        for step in sequence(mapping(job)["steps"])
    ]
    uses = [mapping(step)["uses"] for step in all_steps if "uses" in mapping(step)]

    # Then
    assert all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", text(item)) for item in uses)
    assert "ad-m/github-push-action" not in source
    assert "rm -rf" not in source
    assert not re.search(r"(?<!with-lease)(?:^|\s)--force(?:\s|$)", source)
    assert not re.search(r"git\s+(?:reset|clean|checkout)\b", source)
    assert not re.search(r"(?:rm|mv)\s+[^\n]*\.git", source)
