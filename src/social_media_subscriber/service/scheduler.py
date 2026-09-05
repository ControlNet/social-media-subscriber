"""Cron scheduling, redacted status, and process-group supervision."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol, cast
from zoneinfo import ZoneInfo

import structlog
from apscheduler.triggers.cron import (  # pyright: ignore[reportMissingTypeStubs]
    CronTrigger,
)
from pydantic import Field, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict

from social_media_subscriber.publishing.local import atomic_file
from social_media_subscriber.serialization.json import (
    JsonValue,
    canonical_json_value_bytes,
)

_LOCALTIME = Path("/etc/localtime")
_LOGGER = structlog.stdlib.get_logger()


def schedule_timezone(name: str | None) -> ZoneInfo:
    """Use an explicit zone or the host-mounted TZif file with full DST rules."""
    if name:
        return ZoneInfo(name)
    with _LOCALTIME.open("rb") as localtime:
        return ZoneInfo.from_file(localtime)


class ServiceSettings(BaseSettings):
    """Small environment-configurable container schedule."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(extra="ignore")
    cron_schedule: str = "17 3 * * *"
    timezone: str | None = None
    refresh_on_startup: bool = True
    worker_timeout_seconds: int = Field(default=0, ge=0)


def run_worker(argv: list[str], stop: threading.Event, timeout: int = 0) -> int:
    """Terminate the complete worker/FFmpeg process group on stop or timeout."""
    with subprocess.Popen(argv, shell=False, start_new_session=True) as process:  # noqa: S603 - application-owned argv, no shell
        deadline = time.monotonic() + timeout if timeout else None
        while process.poll() is None:
            if stop.wait(0.2) or (
                deadline is not None and time.monotonic() >= deadline
            ):
                _LOGGER.warning(
                    "service.worker.stopping",
                    reason="shutdown" if stop.is_set() else "timeout",
                )
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    _ = process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                return 130 if stop.is_set() else 124
        return process.returncode


class CronSchedule(Protocol):
    """Typed surface of the APScheduler 3 cron trigger."""

    def get_next_fire_time(
        self, previous_fire_time: datetime | None, now: datetime
    ) -> datetime | None:
        """Find the next scheduled instant."""
        ...


class CronFactory(Protocol):
    """Typed constructor for the upstream untyped cron implementation."""

    def from_crontab(self, expr: str, timezone: ZoneInfo) -> CronSchedule:
        """Parse a standard five-field expression."""
        ...


def serve(settings: ServiceSettings, snapshot: Path, state_dir: Path) -> None:
    """Run one job at a time, never replaying accumulated missed schedule events."""
    stop = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        stop.set()

    _ = signal.signal(signal.SIGTERM, shutdown)
    _ = signal.signal(signal.SIGINT, shutdown)
    trigger = cast("CronFactory", CronTrigger).from_crontab(
        settings.cron_schedule, timezone=schedule_timezone(settings.timezone)
    )
    last_success: str | None = None
    status_path = state_dir / "status.json"
    status: dict[str, JsonValue] = {}
    if status_path.is_file():
        try:
            status = TypeAdapter(dict[str, JsonValue]).validate_json(
                status_path.read_bytes()
            )
            value = status.get("last_success_at")
            if isinstance(value, str):
                last_success = value
        except (OSError, ValueError):
            pass
    startup = settings.refresh_on_startup
    while not stop.is_set():
        now = datetime.now(UTC)
        next_run = now if startup else trigger.get_next_fire_time(None, now)
        if next_run is None:
            return
        status.update(
            {
                "running": False,
                "last_success_at": last_success,
                "next_run_at": next_run.isoformat(),
            }
        )
        atomic_file(state_dir, Path("status.json"), canonical_json_value_bytes(status))
        _LOGGER.info(
            "service.scheduled",
            next_run_at=next_run.isoformat(),
            worker_timeout_seconds=settings.worker_timeout_seconds,
        )
        if stop.wait(max(0, (next_run - datetime.now(UTC)).total_seconds())):
            break
        startup = False
        status.update(
            {"running": True, "last_started_at": datetime.now(UTC).isoformat()}
        )
        atomic_file(state_dir, Path("status.json"), canonical_json_value_bytes(status))
        result = run_worker(
            [
                sys.executable,
                "-m",
                "social_media_subscriber",
                "refresh-local",
                "--snapshot",
                str(snapshot),
                "--state-dir",
                str(state_dir),
            ],
            stop,
            settings.worker_timeout_seconds,
        )
        finished = datetime.now(UTC).isoformat()
        _LOGGER.info("service.worker.finished", exit_code=result)
        if result in (0, 4):
            last_success = finished
        status.update(
            {
                "running": False,
                "exit_code": result,
                "last_finished_at": finished,
                "last_success_at": last_success,
                "next_run_at": None,
            }
        )
        atomic_file(state_dir, Path("status.json"), canonical_json_value_bytes(status))
