"""Scheduling follows a host zone file, including its daylight-saving rules."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import TZPATH

import pytest
from apscheduler.triggers.cron import (  # pyright: ignore[reportMissingTypeStubs]
    CronTrigger,
)

from social_media_subscriber.service import scheduler


def test_default_schedule_uses_system_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TIMEZONE", raising=False)
    assert scheduler.ServiceSettings().timezone is None


@pytest.mark.parametrize(("month", "offset"), [(1, 11), (7, 10)])
def test_host_zone_retains_daylight_saving_rules(
    monkeypatch: pytest.MonkeyPatch, month: int, offset: int
) -> None:
    zonefile = next(
        Path(root) / "Australia/Melbourne"
        for root in TZPATH
        if (Path(root) / "Australia/Melbourne").is_file()
    )
    monkeypatch.setattr(scheduler, "_LOCALTIME", zonefile)
    timezone = scheduler.schedule_timezone(None)
    trigger = cast("scheduler.CronFactory", CronTrigger).from_crontab(
        "17 3 * * *", timezone=timezone
    )
    next_run = trigger.get_next_fire_time(None, datetime(2026, month, 1, tzinfo=UTC))
    assert next_run is not None
    assert (next_run.hour, next_run.minute) == (3, 17)
    assert next_run.utcoffset() == timedelta(hours=offset)


def test_explicit_timezone_overrides_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduler, "_LOCALTIME", tmp_path / "absent")
    monkeypatch.setenv("TIMEZONE", "UTC")
    timezone = scheduler.schedule_timezone(scheduler.ServiceSettings().timezone)
    assert timezone.key == "UTC"
