"""Interrupted local jobs reuse generated media without contacting providers again."""

from __future__ import annotations

import sys
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

import anyio
import pytest
from anyio.lowlevel import checkpoint
from PIL import Image

from social_media_subscriber.application.collect import collect_snapshot
from social_media_subscriber.application.results import CollectionExitCode
from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.media import archive, convert
from social_media_subscriber.media.slots import media_slots
from social_media_subscriber.service import local
from social_media_subscriber.service.scheduler import ServiceSettings, run_worker
from social_media_subscriber.storage.repository import SnapshotRepository
from social_media_subscriber.storage.run_state import MediaFailure, RunState
from social_media_subscriber.storage.snapshot import SnapshotState
from tests.integration._collection_application_support import (
    PERSON_URL,
    ApplicationClient,
    post,
    settings,
)
from tests.unit.test_media_archive import synthetic_post

if TYPE_CHECKING:
    from pathlib import Path

    from social_media_subscriber.application.collect import CollectionRequest
    from social_media_subscriber.application.results import CollectionResult
    from social_media_subscriber.media.slots import MediaSlot

pytestmark = [
    pytest.mark.media_pipeline,
    pytest.mark.usefixtures("enable_media_compression"),
]


def test_worker_has_no_default_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKER_TIMEOUT_SECONDS", raising=False)
    assert ServiceSettings().worker_timeout_seconds == 0
    assert run_worker([sys.executable, "-c", "pass"], threading.Event()) == 0
    stop = threading.Event()
    stop.set()
    assert (
        run_worker([sys.executable, "-c", "import time; time.sleep(30)"], stop) == 130
    )


@pytest.mark.parametrize("pending_retry", [False, True])
def test_interrupted_local_collection_reuses_media_and_saved_posts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending_retry: bool
) -> None:
    public = tmp_path / "public"
    private = tmp_path / "private"
    calls: list[str] = []
    interrupted = True
    if pending_retry:
        old = synthetic_post(2).model_copy(update={"account_profile_url": PERSON_URL})
        account = Account(
            platform=Platform.LINKEDIN,
            kind=AccountKind.PERSON,
            profile_url=PERSON_URL,
            first_seen_at=old.first_seen_at,
        )
        pending = MediaFailure(
            post_id=str(old.id),
            scope="main-images",
            index=0,
            source_url=media_slots(old)[0].source_url,
            failed_runs=1,
            error="download",
        )
        _ = SnapshotRepository(public).write(
            SnapshotState((account,), (old,), RunState(pending_media=(pending,)))
        )

    async def materialize(slot: MediaSlot, destination: Path) -> None:
        assert destination.is_relative_to(private)
        calls.append(slot.source_url)
        if len(calls) == 2 and interrupted:
            _ = await anyio.Path(destination).write_bytes(b"synthetic partial output")
            raise KeyboardInterrupt
        Image.new("RGB", (16, 12), "red").save(destination, "WEBP")

    records = tuple(
        type(post(str(i))).model_validate(
            post(str(i)).payload | {"images": [f"https://media.licdn.com/{i}.png"]}
        )
        for i in range(1, 3)
    )
    client = ApplicationClient(person_posts=records)

    async def collect(command: CollectionRequest) -> CollectionResult:
        return await collect_snapshot(command, lambda _: client)

    monkeypatch.setattr(local, "collect_snapshot", collect)
    monkeypatch.setattr(archive, "materialize_media", materialize)
    with pytest.raises(KeyboardInterrupt):
        _ = local.refresh_local(public, private, settings(PERSON_URL))
    provider_calls = list(client.calls)
    assert not (private / "candidate").exists()
    assert (public / "posts.json").exists() is pending_retry
    assert (private / "work" / "snapshot" / "posts.json").is_file()
    interrupted = False
    result = local.refresh_local(public, private, settings(PERSON_URL))
    assert result is not None
    assert result.exit_code == CollectionExitCode.SUCCESS
    assert client.calls == provider_calls
    assert len(calls) == 3
    assert calls.count(calls[0]) == 1
    assert calls[1] == calls[2]
    saved = SnapshotRepository(public).load_optional()
    assert saved is not None
    assert len(saved.posts) == len(saved.media) == 2
    assert not (private / "work").exists()


@pytest.mark.anyio
async def test_interrupted_video_reuses_complete_input_but_restarts_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    downloads = 0
    encodes = 0
    destination = tmp_path / "0.webm"
    slot = replace(media_slots(synthetic_post())[0], kind="video")

    async def download(_url: str, path: Path, _limit: int) -> None:
        nonlocal downloads
        downloads += 1
        _ = await anyio.Path(path).write_bytes(b"synthetic complete input")

    async def encode(source: Path, output: Path) -> None:
        nonlocal encodes
        encodes += 1
        assert await anyio.Path(source).read_bytes() == b"synthetic complete input"
        if encodes == 1:
            _ = await anyio.Path(output).write_bytes(b"synthetic partial output")
            scope.cancel()
            await checkpoint()
        _ = await anyio.Path(output).write_bytes(b"synthetic complete output")

    monkeypatch.setattr(convert, "download", download)
    monkeypatch.setattr(convert, "_convert_video", encode)
    with anyio.CancelScope() as scope:
        _ = await convert.materialize_media(slot, destination)
    assert scope.cancelled_caught
    assert destination.with_suffix(".input").is_file()
    _ = await convert.materialize_media(slot, destination)
    assert downloads == 1
    assert encodes == 2
    assert destination.read_bytes() == b"synthetic complete output"


@pytest.mark.anyio
async def test_interrupted_download_is_not_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    destination = tmp_path / "0.webm"
    slot = replace(media_slots(synthetic_post())[0], kind="video")

    async def download(_url: str, path: Path, _limit: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            _ = await anyio.Path(path).write_bytes(b"synthetic partial download")
            scope.cancel()
            await checkpoint()
        _ = await anyio.Path(path).write_bytes(b"synthetic complete input")

    async def encode(source: Path, output: Path) -> None:
        assert await anyio.Path(source).read_bytes() == b"synthetic complete input"
        _ = await anyio.Path(output).write_bytes(b"synthetic complete output")

    monkeypatch.setattr(convert, "download", download)
    monkeypatch.setattr(convert, "_convert_video", encode)
    with anyio.CancelScope() as scope:
        _ = await convert.materialize_media(slot, destination)
    assert scope.cancelled_caught
    assert not destination.with_suffix(".input").exists()
    _ = await convert.materialize_media(slot, destination)
    assert calls == 2
