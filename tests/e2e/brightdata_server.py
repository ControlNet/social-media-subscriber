from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Final, Self, TypedDict, final, override
from urllib.parse import parse_qs, urlsplit

from pydantic import TypeAdapter

from tests.e2e.brightdata_server_fixtures import (
    ACTIVE_VALUE,
    CHANGED_PERSON_URL,
    COMPANY_URL,
    MEDIA_CANARY,
    OWNERSHIP_CANARY,
    PERSON_URL,
    REVOKED_VALUE,
    SCHEMA_CANARY,
    PersonPostScenario,
)
from tests.e2e.brightdata_server_scenario import ProviderScenario

__all__: Final = (
    "ACTIVE_VALUE",
    "CHANGED_PERSON_URL",
    "COMPANY_URL",
    "MEDIA_CANARY",
    "OWNERSHIP_CANARY",
    "PERSON_URL",
    "REVOKED_VALUE",
    "SCHEMA_CANARY",
    "FakeBrightDataServer",
    "PersonPostScenario",
    "ProviderScenario",
)


class _RequestEnvelope(TypedDict):
    input: tuple[dict[str, str | bool], ...]
    limit_per_input: None


_REQUEST_BODY: Final[TypeAdapter[_RequestEnvelope]] = TypeAdapter(_RequestEnvelope)
_TRIGGER_BODY: Final = TypeAdapter(tuple[dict[str, str | bool], ...])


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
