"""Local publication uses synthetic providers and generated WebP images only."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from anyio.to_thread import run_sync
from PIL import Image

from social_media_subscriber.application.collect import collect_snapshot
from social_media_subscriber.application.results import CollectionExitCode
from social_media_subscriber.media import archive
from social_media_subscriber.publishing import local as local_publisher
from social_media_subscriber.publishing.local import publish_local
from social_media_subscriber.service import local
from social_media_subscriber.storage.binary import BinaryFile
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.run_state import RunState
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.integration._collection_application_support import (
    ApplicationClient,
    post,
    request,
    settings,
)
from tests.integration.test_media_pipeline import (
    _account,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.test_media_archive import synthetic_post

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from social_media_subscriber.application.collect import CollectionRequest
    from social_media_subscriber.application.results import CollectionResult
    from social_media_subscriber.media.slots import MediaSlot
    from social_media_subscriber.storage.binary import FilePayload

pytestmark = [
    pytest.mark.media_pipeline,
    pytest.mark.usefixtures("enable_media_compression"),
]
_OLD_MEDIA = "media/linkedin/1/main-images/0.webp"
_NEW_MEDIA = "media/linkedin/2/main-images/0.webp"


def _seed(root: Path) -> Path:
    source = root.parent / "synthetic.webp"
    Image.new("RGB", (16, 12)).save(source, "WEBP")
    old = synthetic_post().model_copy(
        update={"content": {"images": [{"url": f"/social-media/{_OLD_MEDIA}"}]}}
    )
    _ = SnapshotRepository(root).write(
        SnapshotState(
            (_account(),),
            (old,),
            RunState(),
            {root.joinpath(_OLD_MEDIA).relative_to(root): BinaryFile.inspect(source)},
        )
    )
    return root / _OLD_MEDIA


def _forbid_historical_reads(monkeypatch: pytest.MonkeyPatch, old: Path) -> None:
    original = BinaryFile.chunks

    def chunks(payload: BinaryFile) -> Iterator[bytes]:
        assert payload.path != old, "Historical media must not be read during refresh"
        yield from original(payload)

    monkeypatch.setattr(BinaryFile, "chunks", chunks)


def test_refresh_does_not_read_or_copy_historical_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public"
    old = _seed(public)
    before = old.stat()
    _forbid_historical_reads(monkeypatch, old)

    async def collect(command: CollectionRequest) -> CollectionResult:
        return await collect_snapshot(
            command, lambda _: ApplicationClient(person_posts=())
        )

    monkeypatch.setattr(local, "collect_snapshot", collect)
    for _ in range(2):
        result = local.refresh_local(
            public, tmp_path / "private", settings(_account().profile_url)
        )
        assert result is not None
        assert result.exit_code == CollectionExitCode.SUCCESS
        assert result.digest is None
        assert not (tmp_path / "private" / "candidate").exists()
    after = old.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.anyio
@pytest.mark.parametrize("interrupted_path", [_NEW_MEDIA, "posts.json"])
@pytest.mark.parametrize("has_history", [True, False])
async def test_candidate_keeps_only_new_media_and_can_be_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted_path: str,
    has_history: bool,
) -> None:
    public = tmp_path / "public"
    old = _seed(public) if has_history else public / _OLD_MEDIA
    _forbid_historical_reads(monkeypatch, old)

    async def materialize(_slot: MediaSlot, destination: Path) -> None:
        Image.new("RGB", (12, 16)).save(destination, "WEBP")

    monkeypatch.setattr(archive, "materialize_media", materialize)
    record = post("2", actor_url=_account().profile_url)
    record = type(record).model_validate(
        record.payload | {"images": ["https://media.licdn.com/synthetic-new"]}
    )
    command = replace(
        request(tmp_path, settings(_account().profile_url)),
        previous_snapshot_dir=public,
        candidate_snapshot_dir=tmp_path / "private" / "candidate",
        local_media_root=public,
    )
    result = await collect_snapshot(
        command, lambda _: ApplicationClient(person_posts=(record,))
    )
    assert result.exit_code == CollectionExitCode.SUCCESS
    candidate = command.candidate_snapshot_dir
    assert not (candidate / _OLD_MEDIA).exists()
    assert (candidate / _NEW_MEDIA).is_file()
    assert not (public / _NEW_MEDIA).exists()
    atomic_file = local_publisher.atomic_file

    def interrupted(
        root: Path, relative: Path, payload: FilePayload, *, immutable: bool = False
    ) -> None:
        atomic_file(root, relative, payload, immutable=immutable)
        if relative.as_posix() == interrupted_path:
            message = "synthetic publication interruption"
            raise OSError(message)

    with monkeypatch.context() as crash:
        crash.setattr(local_publisher, "atomic_file", interrupted)
        with pytest.raises(OSError, match="synthetic publication interruption"):
            publish_local(candidate, public)

    async def next_collection(next_command: CollectionRequest) -> CollectionResult:
        # Recovery finishes and removes the candidate before the next provider call.
        assert not candidate.exists()
        return await collect_snapshot(
            next_command, lambda _: ApplicationClient(person_posts=())
        )

    monkeypatch.setattr(local, "collect_snapshot", next_collection)
    result = await run_sync(
        local.refresh_local,
        public,
        tmp_path / "private",
        settings(_account().profile_url),
    )
    assert result is not None
    assert result.exit_code == CollectionExitCode.SUCCESS
    assert (public / _NEW_MEDIA).is_file()
    assert not candidate.exists()


@pytest.mark.parametrize("damage", ["missing", "symlink", "empty"])
def test_local_inventory_rejects_missing_or_unsafe_media_before_provider_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    public = tmp_path / "public"
    old = _seed(public)
    old.unlink()
    if damage == "symlink":
        old.symlink_to(tmp_path / "synthetic.webp")
    elif damage == "empty":
        old.touch()
    client = ApplicationClient(person_posts=())

    async def collect(command: CollectionRequest) -> CollectionResult:
        return await collect_snapshot(command, lambda _: client)

    monkeypatch.setattr(local, "collect_snapshot", collect)
    result = local.refresh_local(
        public, tmp_path / "private", settings(_account().profile_url)
    )
    assert result is not None
    assert result.exit_code == CollectionExitCode.INTEGRITY
    assert client.calls == []


def test_explicit_verification_still_reads_media_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public = tmp_path / "public"
    old = _seed(public)
    reads: list[Path] = []
    original = BinaryFile.chunks

    def chunks(payload: BinaryFile) -> Iterator[bytes]:
        reads.append(payload.path)
        yield from original(payload)

    monkeypatch.setattr(BinaryFile, "chunks", chunks)
    validated = SnapshotRepository(public).read_optional()
    assert validated is not None
    assert validated.summary.digest is not None
    assert old in reads
