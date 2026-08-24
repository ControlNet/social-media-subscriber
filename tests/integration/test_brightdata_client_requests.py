from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import TYPE_CHECKING

import anyio
import pytest
from anyio.abc import SocketAttribute
from anyio.streams.buffered import BufferedByteReceiveStream
from structlog.testing import capture_logs

from social_media_subscriber.providers.brightdata.client import BrightDataClient
from social_media_subscriber.providers.brightdata.constants import (
    LINKEDIN_POSTS_DATASET,
    POST_RESULTS_PER_INPUT,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.brightdata.requests import PostDiscoveryInput
from social_media_subscriber.providers.http import HttpClientConfig

JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None
type ResponsePayload = JsonValue | bytes

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from anyio.abc import SocketStream
    from anyio.streams.stapled import MultiListener


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    target: str
    authorization_present: bool
    body: JsonValue


@dataclass(frozen=True, slots=True)
class FakeState:
    responses: list[tuple[int, ResponsePayload]]
    requests: list[RecordedRequest] = field(default_factory=list)


async def _handle(stream: SocketStream, state: FakeState) -> None:
    async with stream:
        buffered = BufferedByteReceiveStream(stream)
        head = await buffered.receive_until(b"\r\n\r\n", 65_536)
        lines = head.decode("ascii").split("\r\n")
        method, target, _version = lines[0].split(" ")
        headers = {
            key.casefold(): value.strip()
            for key, value in (line.split(":", 1) for line in lines[1:] if line)
        }
        length = int(headers.get("content-length", "0"))
        payload = await buffered.receive_exactly(length) if length else b""
        body: JsonValue = json.loads(payload) if payload else None
        state.requests.append(
            RecordedRequest(method, target, "authorization" in headers, body)
        )
        status, response = state.responses.pop(0)
        encoded = (
            response
            if isinstance(response, bytes)
            else json.dumps(response, separators=(",", ":")).encode()
        )
        reason = "OK" if status < 400 else "Error"
        response_head = (
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n"
        )
        await stream.send(response_head.encode() + encoded)


async def _serve(
    listener: MultiListener[SocketStream],
    state: FakeState,
) -> None:
    async with listener:
        await listener.serve(partial(_handle, state=state))


@asynccontextmanager
async def fake_server(
    responses: list[tuple[int, ResponsePayload]],
) -> AsyncGenerator[tuple[FakeState, str]]:
    state = FakeState(responses.copy())
    listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
    get_extra = listener.extra
    port: int = get_extra(SocketAttribute.local_port)
    async with anyio.create_task_group() as group:
        _ = group.start_soon(_serve, listener, state)
        try:
            yield state, f"http://127.0.0.1:{port}"
        finally:
            group.cancel_scope.cancel()


def _post(identifier: str = "post-1") -> dict[str, JsonValue]:
    return {
        "id": identifier,
        "date_posted": "2026-08-19T08:15:00+00:00",
        "post_type": "post",
        "url": f"https://www.linkedin.com/posts/{identifier}",
        "user_id": "12345",
        "user_url": "https://www.linkedin.com/in/example/",
    }


def _input() -> PostDiscoveryInput:
    return PostDiscoveryInput(
        url="https://www.linkedin.com/in/example/",
        start_date=date(2026, 8, 19),
        end_date=date(2026, 8, 20),
    )


@pytest.mark.anyio
async def test_company_posts_follow_owned_snapshot_to_ready_download() -> None:
    # Given
    sleeps: list[float] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    request = PostDiscoveryInput(
        url="https://www.linkedin.com/company/example/",
        start_date=date(2026, 8, 13),
        end_date=date(2026, 8, 20),
    )
    responses: list[tuple[int, ResponsePayload]] = [
        (200, {"snapshot_id": "snapshot-safe-id"}),
        (200, {"snapshot_id": "snapshot-safe-id", "status": "running"}),
        (200, {"snapshot_id": "snapshot-safe-id", "status": "ready"}),
        (200, [_post()]),
    ]
    async with fake_server(responses) as (state, base_url):
        client = BrightDataClient(
            "test-secret",
            HttpClientConfig(base_url=base_url),
            sleeper=sleeper,
        )

        # When
        async with client:
            result = await client.collect_company_posts((request,))

    # Then
    assert result[0].id == "post-1"
    assert sleeps == [5.0, 5.0]
    assert [entry.target for entry in state.requests] == [
        (
            "/datasets/v3/trigger?dataset_id="
            f"{LINKEDIN_POSTS_DATASET}&include_errors=true"
            f"&limit_per_input={POST_RESULTS_PER_INPUT}&type=discover_new"
            "&discover_by=company_url"
        ),
        "/datasets/v3/progress/snapshot-safe-id",
        "/datasets/v3/progress/snapshot-safe-id",
        "/datasets/v3/snapshot/snapshot-safe-id?format=json",
    ]
    assert state.requests[0].body == [
        {
            "url": request.url,
            "start_date": "2026-08-13T00:00:00.000Z",
            "end_date": "2026-08-20T23:59:59.999Z",
        }
    ]
    assert all(entry.authorization_present for entry in state.requests)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "malicious_snapshot_id",
    [
        "../../private",
        "../x",
        "/absolute",
        "slash/value",
        "backslash\\value",
        "encoded%2fslash",
        "encoded%5cbackslash",
        "value?query",
        "value#fragment",
        ".",
        "..",
        " leading",
        "trailing ",
        "line\nbreak",
        "unicode-雪",
        "x" * 129,
    ],
)
async def test_snapshot_id_cannot_escape_fixed_provider_endpoints(
    malicious_snapshot_id: str,
) -> None:
    # Given
    async def sleeper(_delay: float) -> None:
        return

    with capture_logs() as logs:
        async with fake_server(
            [
                (200, {"snapshot_id": malicious_snapshot_id}),
                (401, {"error": "provider-canary"}),
            ]
        ) as (state, base_url):
            client = BrightDataClient(
                "credential-canary",
                HttpClientConfig(base_url=base_url),
                sleeper=sleeper,
            )

            # When
            async with client:
                with pytest.raises(BrightDataError) as captured:
                    _ = await client.collect_person_posts((_input(),))

    # Then
    assert captured.value.category is BrightDataErrorCategory.SCHEMA
    assert len(state.requests) == 1
    assert state.requests[0].method == "POST"
    assert not hasattr(captured.value, "snapshot_id")
    assert all("snapshot_id" not in event for event in logs)
    if len(malicious_snapshot_id) > 2:
        assert malicious_snapshot_id not in str(captured.value)
        assert malicious_snapshot_id not in repr(captured.value)
        assert malicious_snapshot_id not in json.dumps(logs, ensure_ascii=False)


@pytest.mark.anyio
async def test_person_posts_accept_jsonl_and_exact_trigger_contract() -> None:
    # Given
    item = _input()
    response = b"\n".join(
        json.dumps(_post(identifier)).encode() for identifier in ("post-1", "post-2")
    )
    async with fake_server([(200, response)]) as (state, base_url):
        client = BrightDataClient("test-secret", HttpClientConfig(base_url=base_url))

        # When
        async with client:
            result = await client.collect_person_posts((item,))

    # Then
    assert [post.id for post in result] == ["post-1", "post-2"]
    assert state.requests[0].target == (
        f"/datasets/v3/trigger?dataset_id={LINKEDIN_POSTS_DATASET}"
        f"&include_errors=true&limit_per_input={POST_RESULTS_PER_INPUT}"
        "&type=discover_new&discover_by=profile_url"
    )
    assert state.requests[0].body == [
        {
            "url": item.url,
            "start_date": "2026-08-19T00:00:00.000Z",
            "end_date": "2026-08-20T23:59:59.999Z",
            "only_authored_posts": True,
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_retryable_statuses_use_exact_bounded_backoff(status: int) -> None:
    # Given
    sleeps: list[float] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    responses: list[tuple[int, ResponsePayload]] = [
        (status, {"error": "provider-canary"}),
        (status, {"error": "provider-canary"}),
        (status, {"error": "provider-canary"}),
    ]
    async with fake_server(responses) as (state, base_url):
        client = BrightDataClient(
            "credential-canary",
            HttpClientConfig(base_url=base_url),
            sleeper=sleeper,
        )

        # When / Then
        async with client:
            with pytest.raises(BrightDataError) as captured:
                _ = await client.collect_person_posts((_input(),))

    assert captured.value.category is BrightDataErrorCategory.RETRYABLE
    assert sleeps == [1.0, 2.0]
    assert len(state.requests) == 3
