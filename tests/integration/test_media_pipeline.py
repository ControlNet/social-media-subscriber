"""Real archive, disk, and publisher integration using synthetic local media."""

from __future__ import annotations

import fcntl
import shutil
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio
import pytest
from anyio.to_thread import run_sync
from PIL import Image

from social_media_subscriber.application.collect import (
    collect_snapshot,
)
from social_media_subscriber.application.results import CollectionExitCode
from social_media_subscriber.application.windows import (
    ExplicitWindow,
    WindowContext,
    build_post_requests,
)
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.media import archive, convert
from social_media_subscriber.media.slots import MediaSlot
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.publishing.git import (
    Published,
    Unchanged,
    publish_snapshot,
)
from social_media_subscriber.publishing.local import publish_local
from social_media_subscriber.service.local import refresh_local
from social_media_subscriber.service.scheduler import run_worker
from social_media_subscriber.storage.binary import BinaryFile
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.run_state import RunState
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.integration._collection_application_support import (
    PERSON_URL,
    ApplicationClient,
    post,
    request,
    settings,
)
from tests.integration.test_dist_publisher import (
    _extract,  # pyright: ignore[reportPrivateUsage]
    _git,  # pyright: ignore[reportPrivateUsage]
    _remote_sha,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _setup_repositories,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.test_media_archive import synthetic_post

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.media_pipeline


def _account() -> Account:
    return Account(
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url="https://www.linkedin.com/in/synthetic/",
        first_seen_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("provider_unavailable", [False, True])
async def test_real_collection_partial_media_then_retry_advances_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider_unavailable: bool
) -> None:
    failures = True
    calls: list[str] = []

    async def materialize(slot: MediaSlot, destination: Path) -> None:
        calls.append(slot.source_url)
        if failures and "image-5" in slot.source_url:
            message = "download"
            raise ValueError(message)
        await run_sync(Image.new("RGB", (16, 12)).save, destination, "WEBP")

    monkeypatch.setattr(archive, "materialize_media", materialize)
    records = tuple(
        post(str(i)).model_copy(
            update={"images": [f"https://media.licdn.com/image-{i}"]}
        )
        for i in range(1, 6)
    )
    # Open-ended provider records are constructed through their validated payload.
    records = tuple(
        type(item).model_validate(
            item.payload | {"images": [f"https://media.licdn.com/image-{i}"]}
        )
        for i, item in enumerate(records, 1)
    )
    client = ApplicationClient(person_posts=records)
    first = await collect_snapshot(
        request(tmp_path, settings(PERSON_URL)), lambda _: client
    )
    assert first.exit_code == CollectionExitCode.PARTIAL
    previous = tmp_path / "candidate"
    saved = SnapshotRepository(previous).load_optional()
    assert saved is not None
    assert saved.run_state is not None
    assert len(saved.posts) == 5
    assert len(saved.run_state.pending_media) == 1
    assert PERSON_URL in saved.run_state.accounts
    failures = False
    next_request = replace(
        request(tmp_path, settings(PERSON_URL)),
        previous_snapshot_dir=previous,
        candidate_snapshot_dir=tmp_path / "second",
    )
    result = await collect_snapshot(
        next_request,
        lambda _: ApplicationClient(
            person_posts=(),
            person_failure=BrightDataError(BrightDataErrorCategory.AUTH, 401)
            if provider_unavailable
            else None,
        ),
    )
    assert result.exit_code == (
        CollectionExitCode.PARTIAL
        if provider_unavailable
        else CollectionExitCode.SUCCESS
    )
    recovered = SnapshotRepository(tmp_path / "second").load_optional()
    assert recovered is not None
    assert recovered.run_state is not None
    assert recovered.run_state.pending_media == ()
    assert len(calls) == 6
    if provider_unavailable:
        assert recovered.run_state.accounts == saved.run_state.accounts


@pytest.mark.anyio
async def test_binary_snapshot_and_local_recovery(tmp_path: Path) -> None:
    async def materialize(slot: MediaSlot, destination: Path) -> None:
        assert slot.kind == "image"
        await run_sync(Image.new("RGB", (12, 16)).save, destination, "WEBP")

    state, _ = await archive.archive_media(
        SnapshotState((_account(),), (synthetic_post(),), RunState()),
        tmp_path / "encoded",
        materialize=materialize,
    )
    candidate = tmp_path / "private" / "candidate"
    _ = SnapshotRepository(candidate).write(state)
    validated = SnapshotRepository(candidate).read_optional()
    assert validated is not None
    assert any(isinstance(value, BinaryFile) for value in validated.files.values())
    destination = tmp_path / "public"
    publish_local(candidate, destination)
    assert (destination / "state.json").stat().st_mode & 0o777 == 0o644
    before = SnapshotRepository(destination).load_optional()
    assert before is not None
    publish_local(candidate, destination)
    after = SnapshotRepository(destination).load_optional()
    assert after == before
    # Simulate a crash during publication; the complete candidate is kept for replay.
    (destination / "posts.json").unlink()
    _ = (destination / ".publishing-interrupted").write_bytes(b"incomplete")
    _ = await run_sync(refresh_local, destination, tmp_path / "private", settings(""))
    assert SnapshotRepository(destination).load_optional() == before
    assert not candidate.exists()
    assert not (destination / ".publishing-interrupted").exists()
    assert before.run_state is not None
    _ = (destination / "state.json").write_text(
        before.run_state.model_dump_json(indent=2)
    )
    assert SnapshotRepository(destination).load_optional() == before


@pytest.mark.anyio
async def test_git_snapshot_contains_media_and_reuses_complete_previous(
    tmp_path: Path,
) -> None:
    async def materialize(slot: MediaSlot, destination: Path) -> None:
        assert slot.kind == "image"
        await run_sync(Image.new("RGB", (12, 16)).save, destination, "WEBP")

    state, _ = await archive.archive_media(
        SnapshotState((_account(),), (synthetic_post(),), RunState()),
        tmp_path / "encoded",
        materialize=materialize,
    )
    candidate = tmp_path / "candidate"
    _ = SnapshotRepository(candidate).write(state)
    source, remote = _setup_repositories(tmp_path)
    result = publish_snapshot(_request(source, candidate, "absent"))
    assert isinstance(result, Published)
    sha = _remote_sha(remote)
    assert "media/linkedin/1/main-images/0.webp" in _git(
        remote, "ls-tree", "-r", "--name-only", sha
    )
    previous = tmp_path / "previous"
    _extract(remote, sha, previous)
    result = publish_snapshot(_request(source, candidate, sha, previous))
    assert isinstance(result, Unchanged)
    assert (
        SnapshotRepository(previous).load_optional()
        == SnapshotRepository(candidate).load_optional()
    )


def test_watermark_catches_up_after_long_gap() -> None:
    account = _account()
    progress = RunState(accounts={str(account.id): datetime(2026, 8, 1, tzinfo=UTC)})
    state = SnapshotState((account,), (), progress)
    requests = build_post_requests(
        (account,),
        state,
        WindowContext(datetime(2026, 9, 1, tzinfo=UTC), ExplicitWindow(None, None)),
    )
    assert requests[0].start_date.isoformat() == "2026-07-29"
    assert requests[0].end_date.isoformat() == "2026-09-01"


def test_local_lock_skips_overlapping_worker(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir()
    with (private / "run.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert refresh_local(tmp_path / "public", private, settings("")) is None


def test_worker_timeout_and_stop() -> None:
    assert (
        run_worker(
            [sys.executable, "-c", "import time; time.sleep(30)"], threading.Event(), 1
        )
        == 124
    )
    stop = threading.Event()
    stop.set()
    assert (
        run_worker([sys.executable, "-c", "import time; time.sleep(30)"], stop, 30)
        == 130
    )


def test_image_conversion_preserves_dimensions_and_alpha(tmp_path: Path) -> None:
    source = tmp_path / "synthetic.png"
    output = tmp_path / "output.webp"
    Image.new("RGBA", (123, 57), (255, 0, 0, 100)).save(source)
    convert.convert_image(source, output)
    with Image.open(output) as result:
        assert result.size == (123, 57)
        assert result.mode == "RGBA"


@pytest.mark.anyio
async def test_video_conversion_uses_real_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "synthetic.mp4"
    _ = await anyio.run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:r=10:d=0.2",
            "-c:v",
            "libx264",
            str(source),
        ]
    )

    async def download(url: str, target: Path, limit: int) -> None:
        assert url.startswith("https://")
        assert limit > 0
        _ = await run_sync(shutil.copyfile, source, target)

    monkeypatch.setattr(convert, "download", download)

    target = tmp_path / "output.webm"
    await convert.materialize_media(
        MediaSlot(
            "main-videos",
            0,
            ("videos", 0, "url"),
            "https://video.twimg.com/synthetic.mp4",
            "video",
        ),
        target,
    )
    result = await anyio.run_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "csv=p=0",
            str(target),
        ]
    )
    assert b"vp9,64,48" in result.stdout
