"""Stream numeric FFmpeg progress without exposing source paths or decoder text."""

from __future__ import annotations

import math
import subprocess
import time
from typing import Literal

import anyio
import structlog
from anyio.streams.text import TextReceiveStream

from social_media_subscriber.media.errors import MediaError

_LOGGER = structlog.stdlib.get_logger()
PROGRESS_INTERVAL = 30
_FIELDS = {
    "frame": "frames",
    "fps": "fps",
    "out_time_us": "media_seconds",
    "speed": "speed_x",
}


def update_progress(line: str, values: dict[str, float]) -> None:
    """Accept numeric fields only; FFmpeg may report N/A before its first frame."""
    key, _, raw = line.partition("=")
    if key not in _FIELDS:
        return
    try:
        value = float(raw.removesuffix("x"))
    except ValueError:
        return
    if math.isfinite(value) and value >= 0:
        values[_FIELDS[key]] = round(
            value / 1_000_000 if key == "out_time_us" else value, 3
        )


async def run_ffmpeg(
    arguments: list[str], *, phase: Literal["encode", "validate"]
) -> None:
    """Report a heartbeat even when FFmpeg has not emitted another progress block."""
    started = time.monotonic()
    values: dict[str, float] = {}

    async def heartbeat() -> None:
        while True:
            await anyio.sleep(PROGRESS_INTERVAL)
            await _LOGGER.ainfo(
                "media.video.progress",
                phase=phase,
                elapsed_seconds=round(time.monotonic() - started, 1),
                **values,
            )

    await _LOGGER.ainfo("media.video.started", phase=phase)
    async with await anyio.open_process(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-nostats",
            "-stats_period",
            "10",
            "-progress",
            "pipe:1",
            *arguments,
        ],
        stderr=subprocess.DEVNULL,
    ) as process:
        if process.stdout is None:
            raise MediaError(category="conversion")
        async with anyio.create_task_group() as group:
            _ = group.start_soon(heartbeat)
            buffer = ""
            async for chunk in TextReceiveStream(process.stdout):
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    update_progress(line.strip(), values)
            group.cancel_scope.cancel()
        code = await process.wait()
    if code:
        await _LOGGER.awarning("media.video.failed", phase=phase, exit_code=code)
        raise MediaError(category="conversion")
    await _LOGGER.ainfo(
        "media.video.completed",
        phase=phase,
        elapsed_seconds=round(time.monotonic() - started, 1),
        **values,
    )
