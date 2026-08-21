from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Final, Self, TypedDict, final, override
from urllib.parse import parse_qs, urlsplit

from pydantic import TypeAdapter

from social_media_subscriber.providers.brightdata.constants import (
    COMPANY_IDENTITY_DATASET,
    LINKEDIN_POSTS_DATASET,
    PERSON_IDENTITY_DATASET,
)

PERSON_URL: Final = "https://www.linkedin.com/in/synthetic-ada/"
COMPANY_URL: Final = "https://www.linkedin.com/company/synthetic-labs/"
REVOKED_VALUE: Final = "task14-revoked-test-value"
ACTIVE_VALUE: Final = "task14-active-test-value"
MEDIA_CANARY: Final = (
    "https://media.licdn.com/dms/image/task14?signature=source-only-canary"
)
type FakeJson = dict[str, str] | list[dict[str, str | int | object]]


class _RequestEnvelope(TypedDict):
    input: tuple[dict[str, str | bool], ...]
    limit_per_input: None


_REQUEST_BODY: Final[TypeAdapter[_RequestEnvelope]] = TypeAdapter(_RequestEnvelope)
_TRIGGER_BODY: Final = TypeAdapter(tuple[dict[str, str | bool], ...])


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    credential: str
    dataset: str
    discovery: str | None
    body: tuple[dict[str, str | bool], ...]


@dataclass(slots=True)
class ProviderScenario:
    metric: int = 42
    fail_person_posts: bool = False
    invalid_identity_schema: bool = False
    accepted_snapshot_failure: bool = False
    requests: list[ProviderRequest] = field(default_factory=list)

    def response(
        self,
        authorization: str,
        dataset: str,
        discovery: str | None,
        body: tuple[dict[str, str | bool], ...],
    ) -> tuple[HTTPStatus, FakeJson]:
        credential = _credential_label(authorization)
        self.requests.append(ProviderRequest(credential, dataset, discovery, body))
        if credential == "revoked":
            return HTTPStatus.TOO_MANY_REQUESTS, {"status": "quota"}
        identity = self._identity_response(dataset)
        if identity is not None:
            return identity
        posts = self._post_response(dataset, discovery)
        if posts is not None:
            return posts
        return HTTPStatus.BAD_REQUEST, {"status": "unsupported"}

    def _identity_response(self, dataset: str) -> tuple[HTTPStatus, FakeJson] | None:
        if dataset == PERSON_IDENTITY_DATASET:
            if self.invalid_identity_schema:
                return HTTPStatus.OK, [{"linkedin_num_id": 101}]
            return HTTPStatus.OK, [{"linkedin_num_id": "101", "url": PERSON_URL}]
        if dataset == COMPANY_IDENTITY_DATASET:
            return HTTPStatus.OK, [{"company_id": "202", "url": COMPANY_URL}]
        return None

    def _post_response(
        self, dataset: str, discovery: str | None
    ) -> tuple[HTTPStatus, FakeJson] | None:
        if dataset != LINKEDIN_POSTS_DATASET:
            return None
        if discovery == "profile_url":
            if self.fail_person_posts:
                return HTTPStatus.NOT_FOUND, {"status": "not_found"}
            if self.accepted_snapshot_failure:
                return HTTPStatus.OK, {"snapshot_id": "accepted-person"}
            return HTTPStatus.OK, _person_posts(self.metric)
        if discovery == "company_url":
            return HTTPStatus.OK, _company_posts()
        return None


class _ScenarioHttpServer(HTTPServer):
    scenario: ProviderScenario

    def __init__(self, scenario: ProviderScenario) -> None:
        self.scenario = scenario
        super().__init__(("127.0.0.1", 0), _Handler)


@final
class _Handler(BaseHTTPRequestHandler):
    server_version: str = "Task14FakeBrightData/1"

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        match parsed.path:
            case "/datasets/v3/scrape":
                body = _REQUEST_BODY.validate_json(payload)["input"]
            case "/datasets/v3/trigger":
                body = _TRIGGER_BODY.validate_json(payload)
            case _:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        query = parse_qs(parsed.query)
        dataset = query.get("dataset_id", [""])[0]
        discovery = query.get("discover_by", [None])[0]
        match self.server:
            case _ScenarioHttpServer() as server:
                status, payload = server.scenario.response(
                    self.headers.get("Authorization", ""),
                    dataset,
                    discovery,
                    body,
                )
            case unreachable:
                raise AssertionError(type(unreachable).__name__)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        _ = self.wfile.write(encoded)

    def do_GET(self) -> None:
        payload = b'{"snapshot_id":"accepted-person","unexpected":true}'
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        _ = self.wfile.write(payload)

    @override
    def log_message(self, format: str, *args: object) -> None:
        _ = format, args


@dataclass(slots=True)
class FakeBrightDataServer:
    scenario: ProviderScenario = field(default_factory=ProviderScenario)
    _server: _ScenarioHttpServer | None = field(default=None, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        match self._server.server_address:
            case (str() as host, int() as port):
                pass
            case unreachable:
                raise AssertionError(type(unreachable).__name__)
        return f"http://{host}:{port}"

    def __enter__(self) -> Self:
        server = _ScenarioHttpServer(self.scenario)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self._server is not None
        assert self._thread is not None
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()


def _credential_label(authorization: str) -> str:
    if authorization == f"Bearer {REVOKED_VALUE}":
        return "revoked"
    if authorization == f"Bearer {ACTIVE_VALUE}":
        return "active"
    return "unknown"


def _person_posts(metric: int) -> list[dict[str, str | int | object]]:
    return [
        {
            "id": "urn:li:activity:1001",
            "date_posted": "2026-08-19T09:30:00Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-1001/?trk=x",
            "user_id": "101",
            "post_text": "Synthetic original post",
            "num_likes": metric,
            "images": [MEDIA_CANARY],
            "embedded_links": [
                MEDIA_CANARY,
                "https://example.test/public?utm_source=x",
            ],
            "unknown_nested": {"future": [True, None, {"n": 3}]},
        },
        {
            "id": "urn:li:activity:1002",
            "date_posted": "2026-08-19T10:30:00Z",
            "post_type": "future_provider_variant",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-1002/",
            "user_id": "101",
            "post_text": "Source preserved, canonical skipped",
            "future_field": {"preserved": True},
        },
    ]


def _company_posts() -> list[dict[str, str | int | object]]:
    return [
        {
            "id": "urn:li:activity:2001",
            "date_posted": "2026-08-18T08:00:00Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-labs_example-2001/",
            "user_id": "202",
            "post_text": "Synthetic company post",
        }
    ]
