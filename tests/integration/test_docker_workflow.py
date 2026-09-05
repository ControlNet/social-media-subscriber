"""Docker publication gates are exercised locally without registry access."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.workflow_helpers import load_workflow, mapping, sequence, text

_WORKFLOW = Path(__file__).parents[2] / ".github/workflows/ci.yml"


def test_docker_publication_requires_verified_default_branch_push() -> None:
    workflow = load_workflow(_WORKFLOW)
    job = mapping(mapping(workflow["jobs"])["docker"])
    assert job["needs"] == "verify"
    assert job["if"] == (
        "github.event_name == 'push' && "
        "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
    )
    assert mapping(job["permissions"]) == {"contents": "read"}
    steps = [mapping(step) for step in sequence(job["steps"])]
    assert steps[0]["id"] == "gate"
    for step in steps[1:]:
        assert step["if"] == "steps.gate.outputs.enabled == 'true'"
    checkout = steps[1]
    assert mapping(checkout["with"])["persist-credentials"] is False
    build = steps[2]
    assert "--platform linux/amd64" in text(build["run"])
    assert "DOCKERHUB_PASSWORD" not in str(build)
    publication = steps[3]
    assert "--password-stdin" in text(publication["run"])
    assert 'docker push "$IMAGE"' in text(publication["run"])
    environment = mapping(publication["env"])
    reference = "${{ secrets.DOCKERHUB_PASSWORD }}"
    assert environment["DOCKERHUB_PASSWORD"] == reference
    assert environment["IMAGE"] == (
        "${{ secrets.DOCKERHUB_USERNAME }}/social-media-subscriber:latest"
    )


@pytest.mark.parametrize(
    ("has_username", "has_password"),
    [(False, False), (True, False), (False, True), (True, True)],
)
def test_missing_docker_credentials_skip_successfully(
    tmp_path: Path, *, has_username: bool, has_password: bool
) -> None:
    workflow = load_workflow(_WORKFLOW)
    job = mapping(mapping(workflow["jobs"])["docker"])
    gate = mapping(sequence(job["steps"])[0])
    assert mapping(gate["env"]) == {
        "HAS_USERNAME": "${{ secrets.DOCKERHUB_USERNAME != '' }}",
        "HAS_PASSWORD": "${{ secrets.DOCKERHUB_PASSWORD != '' }}",
    }
    bash = shutil.which("bash")
    assert bash is not None
    output = tmp_path / "github-output"
    result = subprocess.run(  # noqa: S603 - local, repository-owned gate script
        [bash, "-c", text(gate["run"])],
        env={
            "GITHUB_OUTPUT": str(output),
            "HAS_USERNAME": str(has_username).lower(),
            "HAS_PASSWORD": str(has_password).lower(),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert (
        output.read_text() == f"enabled={str(has_username and has_password).lower()}\n"
    )
