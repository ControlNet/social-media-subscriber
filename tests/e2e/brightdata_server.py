from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from enum import StrEnum
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
CHANGED_PERSON_URL: Final = "https://www.linkedin.com/in/synthetic-ada-renamed/"
COMPANY_URL: Final = "https://www.linkedin.com/company/synthetic-labs/"
REVOKED_VALUE: Final = "task14-revoked-test-value"
ACTIVE_VALUE: Final = "task14-active-test-value"
MEDIA_CANARY: Final = (
    "https://media.licdn.com/dms/image/task14?signature=source-only-canary"
)
OWNERSHIP_CANARY: Final = "https://www.linkedin.com/in/private-owner-canary/"
SCHEMA_CANARY: Final = "provider-schema-canary"
_PERSON_SNAPSHOT: Final = "person-snapshot"
_COMPANY_SNAPSHOT: Final = "company-snapshot"
type FakeJson = dict[str, str] | list[dict[str, str | int | object]]


class PersonPostScenario(StrEnum):
    SUCCESS = "success"
    ZERO = "zero"
    NONORIGINAL_ONLY = "nonoriginal_only"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    INVALID_SCHEMA = "invalid_schema"


class _RequestEnvelope(TypedDict):
    input: tuple[dict[str, str | bool], ...]
    limit_per_input: None


_REQUEST_BODY: Final[TypeAdapter[_RequestEnvelope]] = TypeAdapter(_RequestEnvelope)
_TRIGGER_BODY: Final = TypeAdapter(tuple[dict[str, str | bool], ...])


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    method: str
    endpoint: str
    credential: str
    dataset: str
    discovery: str | None
    body: tuple[dict[str, str | bool], ...]


@dataclass(slots=True)
class ProviderScenario:
    metric: int = 42
    fail_person_posts: bool = False
    accepted_snapshot_failure: bool = False
    person_result: PersonPostScenario = PersonPostScenario.SUCCESS
    person_actor_url: str = PERSON_URL
    requests: list[ProviderRequest] = field(default_factory=list)
    trigger_calls: int = 0
    progress_calls: int = 0
    download_calls: int = 0
    scrape_calls: int = 0
    identity_calls: int = 0

    def post_response(
        self,
        authorization: str,
        endpoint: str,
        dataset: str,
        discovery: str | None,
        body: tuple[dict[str, str | bool], ...],
    ) -> tuple[HTTPStatus, FakeJson]:
        credential = _credential_label(authorization)
        self.requests.append(
            ProviderRequest("POST", endpoint, credential, dataset, discovery, body)
        )
        if endpoint == "scrape":
            self.scrape_calls += 1
            if dataset in {PERSON_IDENTITY_DATASET, COMPANY_IDENTITY_DATASET}:
                self.identity_calls += 1
        else:
            self.trigger_calls += 1
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
            if self.person_result is PersonPostScenario.INVALID_SCHEMA:
                return HTTPStatus.OK, [{"id": SCHEMA_CANARY}]
            return HTTPStatus.OK, {"snapshot_id": _PERSON_SNAPSHOT}
        if discovery == "company_url":
            return HTTPStatus.OK, {"snapshot_id": _COMPANY_SNAPSHOT}
        return None

    def get_response(
        self,
        authorization: str,
        endpoint: str,
        snapshot_id: str,
    ) -> tuple[HTTPStatus, FakeJson]:
        credential = _credential_label(authorization)
        self.requests.append(
            ProviderRequest(
                "GET",
                endpoint,
                credential,
                LINKEDIN_POSTS_DATASET,
                None,
                (),
            )
        )
        if endpoint == "progress":
            self.progress_calls += 1
            status = "failed" if self.accepted_snapshot_failure else "ready"
            return HTTPStatus.OK, {"status": status}
        self.download_calls += 1
        if snapshot_id == _PERSON_SNAPSHOT:
            return HTTPStatus.OK, self._person_download()
        if snapshot_id == _COMPANY_SNAPSHOT:
            return HTTPStatus.OK, _company_posts()
        return HTTPStatus.NOT_FOUND, {"status": "not_found"}

    def _person_download(self) -> FakeJson:
        match self.person_result:
            case PersonPostScenario.SUCCESS:
                return _person_posts(self.metric, self.person_actor_url)
            case PersonPostScenario.ZERO:
                return []
            case PersonPostScenario.NONORIGINAL_ONLY:
                return [_person_repost(self.person_actor_url)]
            case PersonPostScenario.OWNERSHIP_CONFLICT:
                return _person_posts(self.metric, OWNERSHIP_CANARY)
            case PersonPostScenario.INVALID_SCHEMA:
                raise AssertionError


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
                endpoint = "scrape"
            case "/datasets/v3/trigger":
                body = _TRIGGER_BODY.validate_json(payload)
                endpoint = "trigger"
            case _:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        query = parse_qs(parsed.query)
        dataset = query.get("dataset_id", [""])[0]
        discovery = query.get("discover_by", [None])[0]
        match self.server:
            case _ScenarioHttpServer() as server:
                status, payload = server.scenario.post_response(
                    self.headers.get("Authorization", ""),
                    endpoint,
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
        parsed = urlsplit(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["datasets", "v3"]:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        match parts[2]:
            case "progress":
                endpoint = "progress"
            case "snapshot":
                endpoint = "download"
            case _:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        match self.server:
            case _ScenarioHttpServer() as server:
                status, response = server.scenario.get_response(
                    self.headers.get("Authorization", ""), endpoint, parts[3]
                )
            case unreachable:
                raise AssertionError(type(unreachable).__name__)
        payload = json.dumps(response, separators=(",", ":")).encode()
        self.send_response(status)
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

    @property
    def thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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


def _person_posts(metric: int, actor_url: str) -> list[dict[str, str | int | object]]:
    return [
        {
            "id": "urn:li:activity:1001",
            "date_posted": "2026-08-19T09:30:00Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-1001/?trk=x",
            "user_id": "101",
            "post_text": "Synthetic original post",
            "num_likes": metric,
            "profile_url": actor_url,
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
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-1002/",
            "user_id": "101",
            "profile_url": actor_url,
            "post_text": "Synthetic second original post",
            "future_field": {"preserved": True},
        },
        {
            "id": "urn:li:activity:1003",
            "date_posted": "2026-08-19T11:30:00Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-1003/",
            "user_id": "101",
            "profile_url": actor_url,
            "post_text": "Synthetic third original post",
        },
        _person_repost(actor_url),
    ]


def _person_repost(actor_url: str) -> dict[str, str | int | object]:
    return {
        "id": "urn:li:activity:1004",
        "date_posted": "2026-08-19T12:30:00Z",
        "post_type": "repost",
        "url": "https://www.linkedin.com/posts/synthetic-ada_example-1004/",
        "user_id": "101",
        "profile_url": actor_url,
        "post_text": "Synthetic repost retained only as source",
    }


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
