"""Progress logs use generated media and synthetic providers, never live services."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest
from structlog.testing import capture_logs

from social_media_subscriber.media import convert, ffmpeg
from social_media_subscriber.providers.apify.runner import ApifyActorRunner

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Literal


@pytest.mark.anyio
async def test_cli_logging_filters_debug_and_preserves_stdout() -> None:
    result = await anyio.run_process(
        [
            sys.executable,
            "-c",
            (
                "import structlog; "
                "from social_media_subscriber.cli_logging import configure_logging; "
                "configure_logging(); "
                "structlog.contextvars.bind_contextvars(post_id='synthetic-post'); "
                "log = structlog.get_logger(); "
                "log.debug('synthetic-debug'); log.info('synthetic-progress')"
            ),
        ]
    )
    assert result.stdout == b""
    assert b"synthetic-debug" not in result.stderr
    assert b"synthetic-progress" in result.stderr
    assert b"post_id=synthetic-post" in result.stderr


@pytest.mark.anyio
async def test_real_video_reports_encoding_and_validation_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

    async def automatic_threads(
        arguments: list[str], *, phase: Literal["encode", "validate"]
    ) -> None:
        assert "-threads" not in arguments
        await ffmpeg.run_ffmpeg(arguments, phase=phase)

    monkeypatch.setattr(convert, "run_ffmpeg", automatic_threads)
    with capture_logs() as logs:
        await convert._convert_video(source, tmp_path / "output.webm")  # pyright: ignore[reportPrivateUsage]
    completed = [item for item in logs if item["event"] == "media.video.completed"]
    assert [item["phase"] for item in completed] == ["encode", "validate"]
    assert all(item["frames"] > 0 for item in completed)
    assert all(item["media_seconds"] > 0 for item in completed)
    assert str(tmp_path) not in str(logs)


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_video_heartbeat_and_cancellation(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    monkeypatch.setattr(ffmpeg, "PROGRESS_INTERVAL", 0.02)
    arguments = [
        "-re",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x48:r=10:d=60"
        if cancel
        else "color=c=blue:s=64x48:r=10:d=0.5",
        "-f",
        "null",
        "-",
    ]
    with capture_logs() as logs, anyio.move_on_after(0.5 if cancel else 10) as scope:
        await ffmpeg.run_ffmpeg(arguments, phase="encode")
    assert scope.cancelled_caught is cancel
    assert any(item["event"] == "media.video.progress" for item in logs)
    assert any(item["event"] == "media.video.completed" for item in logs) is not cancel


def test_progress_parser_ignores_non_numeric_and_unrecognized_fields() -> None:
    values: dict[str, float] = {}
    for line in (
        "source=synthetic-private-url",
        "speed=N/A",
        "fps=nan",
        "frame=-1",
        "out_time_us=1500000",
        "speed=0.5x",
    ):
        ffmpeg.update_progress(line, values)
    assert values == {"media_seconds": 1.5, "speed_x": 0.5}


@pytest.mark.anyio
async def test_apify_progress_is_meaningful_and_does_not_leak_response_data() -> None:
    polls = 0

    async def sleeper(_delay: float) -> None:
        return

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal polls
        if request.method == "GET":
            polls += 1
        return httpx2.Response(
            200,
            json={
                "data": {
                    "id": "synthetic-private-run",
                    "status": "RUNNING" if polls < 2 else "SUCCEEDED",
                    "defaultDatasetId": "synthetic-private-dataset",
                }
            },
        )

    with capture_logs() as logs:
        async with ApifyActorRunner(
            "synthetic-private-token",
            sleeper=sleeper,
            transport=httpx2.MockTransport(handler),
        ) as runner:
            _ = await runner.run("synthetic-actor", {})
    states = [
        item["status"] for item in logs if item["event"] == "provider.run.progress"
    ]
    assert states == ["RUNNING", "SUCCEEDED"]
    assert "unsupported" not in str(logs)
    assert "synthetic-private" not in str(logs)
