"""Media transport tests use a synthetic HTTP transport and no network."""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import httpx2
import pytest

from social_media_subscriber.media import convert
from social_media_subscriber.media.convert import (
    _download_once,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.anyio
async def test_temporary_download_errors_retry_three_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def fail(
        client: httpx2.AsyncClient, url: str, target: Path, limit: int
    ) -> None:
        nonlocal calls
        assert not client.is_closed
        assert target.parent == tmp_path
        assert limit == 100
        calls += 1
        message = "synthetic sensitive transport text"
        raise httpx2.ConnectError(message, request=httpx2.Request("GET", url))

    async def sleep(delay: float) -> None:
        assert delay in (1, 2)

    monkeypatch.setattr(convert, "_download_once", fail)
    monkeypatch.setattr(anyio, "sleep", sleep)
    with pytest.raises(ValueError, match=r"^download$"):
        await convert.download(
            "https://pbs.twimg.com/synthetic", tmp_path / "input", 100
        )
    assert calls == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scenario", "category"),
    [("redirect", "source"), ("mime", "mime"), ("size", "size"), ("empty", "empty")],
)
async def test_download_validates_redirect_mime_and_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str, category: str
) -> None:
    async def resolve(
        host: str,
        port: int,
        *,
        type: int,  # noqa: A002 - mirror socket keyword
    ) -> tuple[tuple[int, int, int, str, tuple[str, int]], ...]:
        assert host == "pbs.twimg.com"
        return ((2, type, 6, "", ("8.8.8.8", port)),)

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "pbs.twimg.com"
        if scenario == "redirect":
            return httpx2.Response(
                302, headers={"location": "http://127.0.0.1/private"}
            )
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/html" if scenario == "mime" else "image/webp"
            },
            content=b"" if scenario == "empty" else b"synthetic",
        )

    monkeypatch.setattr(anyio, "getaddrinfo", resolve)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match=f"^{category}$"):
            await _download_once(
                client, "https://pbs.twimg.com/synthetic", tmp_path / "input", 2
            )
