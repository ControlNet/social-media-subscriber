"""Shared tuned asynchronous HTTP transport."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Final

import httpx2
import structlog

_DEFAULT_BASE_URL: Final = "https://api.brightdata.com"
_LIMITS: Final = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
_TIMEOUT: Final = httpx2.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)
_SOCKET_OPTIONS: Final = ((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),)
_LOGGER = structlog.stdlib.get_logger()


@dataclass(frozen=True, slots=True)
class HttpClientConfig:
    """Connection configuration with production-safe defaults."""

    base_url: str = _DEFAULT_BASE_URL


_DEFAULT_CONFIG: Final = HttpClientConfig()


def _endpoint_name(path: str) -> str:
    exact = {"/datasets/v3/scrape": "scrape", "/datasets/v3/trigger": "trigger"}
    if path in exact:
        return exact[path]
    for prefix, name in (
        ("/datasets/v3/progress/", "snapshot_progress"),
        ("/datasets/v3/snapshot/", "snapshot_download"),
        ("/v2/actor-runs/", "actor_progress"),
        ("/v2/key-value-stores/", "actor_record"),
    ):
        if path.startswith(prefix):
            return name
    if path.startswith("/v2/acts/") and path.endswith("/runs"):
        return "actor_start"
    if path.startswith("/v2/datasets/") and path.endswith("/items"):
        return "actor_dataset"
    return "unsupported"


async def _log_request(request: httpx2.Request) -> None:
    await _LOGGER.adebug(
        "provider.http.request",
        method=request.method,
        endpoint=_endpoint_name(request.url.path),
    )


async def _log_response(response: httpx2.Response) -> None:
    await _LOGGER.adebug(
        "provider.http.response",
        method=response.request.method,
        endpoint=_endpoint_name(response.request.url.path),
        status=response.status_code,
    )


def create_async_http_client(
    api_key: str,
    config: HttpClientConfig = _DEFAULT_CONFIG,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> httpx2.AsyncClient:
    """Create one credential-bound client with the required transport tuning."""
    selected_transport = transport or httpx2.AsyncHTTPTransport(
        http2=True, retries=3, limits=_LIMITS, socket_options=_SOCKET_OPTIONS
    )
    return httpx2.AsyncClient(
        base_url=config.base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        transport=selected_transport,
        timeout=_TIMEOUT,
        follow_redirects=True,
        event_hooks={"request": [_log_request], "response": [_log_response]},
    )
