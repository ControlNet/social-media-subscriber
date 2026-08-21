from __future__ import annotations

import shutil
from datetime import date
from typing import TYPE_CHECKING

import pytest

from social_media_subscriber.adapters.router_outcomes import RouterRunStatus
from social_media_subscriber.application.results import CollectionExitCode
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from tests.integration._collection_application_support import (
    PERSON_URL,
    ApplicationClient,
    post,
    request,
    run,
    settings,
    tree,
)

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.storage.snapshot import SnapshotState


@pytest.mark.anyio
async def test_corrupt_prior_and_invalid_override_are_preflight_failures(
    tmp_path: Path,
) -> None:
    # Given
    previous = tmp_path / "previous"
    previous.mkdir()
    _ = (previous / "snapshot.json").write_text("not-json")

    # When
    corrupt = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    invalid = await run(
        request(
            tmp_path,
            settings(PERSON_URL),
            paths=("absent", "invalid"),
            window=(date(2026, 8, 1), None),
        ),
        (ApplicationClient(),),
    )

    # Then
    assert corrupt.exit_code is CollectionExitCode.INTEGRITY
    assert invalid.exit_code is CollectionExitCode.INPUT
    assert not (tmp_path / "candidate").exists()
    assert not (tmp_path / "invalid").exists()


@pytest.mark.anyio
async def test_explicit_window_replaces_defaults(tmp_path: Path) -> None:
    # Given
    client = ApplicationClient()

    # When
    result = await run(
        request(
            tmp_path,
            settings(PERSON_URL),
            window=(date(2026, 7, 1), date(2026, 7, 2)),
        ),
        (client,),
    )

    # Then
    assert result.exit_code is CollectionExitCode.SUCCESS
    assert client.calls[-1] == (
        "person_posts",
        ((date(2026, 7, 1), date(2026, 7, 2)),),
    )


@pytest.mark.anyio
async def test_write_fault_returns_integrity_and_preserves_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    _ = await run(request(tmp_path, settings(PERSON_URL)), (ApplicationClient(),))
    _ = shutil.copytree(tmp_path / "candidate", tmp_path / "previous")
    prior = tree(tmp_path / "previous")

    def fail_write(self: SnapshotRepository, state: SnapshotState) -> None:
        _ = self, state
        reason = "synthetic write interruption"
        raise SnapshotIntegrityError(reason)

    monkeypatch.setattr(SnapshotRepository, "write", fail_write)

    # When
    result = await run(
        request(tmp_path, settings(PERSON_URL), paths=("previous", "fault")),
        (ApplicationClient(person_posts=(post(text="Edited"),)),),
    )

    # Then
    assert result.exit_code is CollectionExitCode.INTEGRITY
    assert tree(tmp_path / "previous") == prior
    assert not (tmp_path / "fault").exists()
    assert "synthetic-key" not in repr(result)
    assert PERSON_URL not in repr(result)


def test_router_status_enum_remains_closed() -> None:
    # Given / When / Then
    assert tuple(RouterRunStatus) == (
        RouterRunStatus.SUCCESS,
        RouterRunStatus.PARTIAL,
        RouterRunStatus.ABORTED,
    )
