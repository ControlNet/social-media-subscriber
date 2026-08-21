from __future__ import annotations

from dataclasses import dataclass, field
from http import HTTPStatus

from social_media_subscriber.providers.brightdata.constants import (
    COMPANY_IDENTITY_DATASET,
    LINKEDIN_POSTS_DATASET,
    PERSON_IDENTITY_DATASET,
)
from tests.e2e.brightdata_server_fixtures import (
    ACTIVE_VALUE,
    COMPANY_SNAPSHOT,
    COMPANY_URL,
    OWNERSHIP_CANARY,
    PERSON_SNAPSHOT,
    PERSON_URL,
    REVOKED_VALUE,
    SCHEMA_CANARY,
    FakeJson,
    PersonPostScenario,
    company_posts,
    person_posts,
    person_repost,
)


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
        credential = credential_label(authorization)
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
            return HTTPStatus.OK, {"snapshot_id": PERSON_SNAPSHOT}
        if discovery == "company_url":
            return HTTPStatus.OK, {"snapshot_id": COMPANY_SNAPSHOT}
        return None

    def get_response(
        self,
        authorization: str,
        endpoint: str,
        snapshot_id: str,
    ) -> tuple[HTTPStatus, FakeJson]:
        credential = credential_label(authorization)
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
        if snapshot_id == PERSON_SNAPSHOT:
            return HTTPStatus.OK, self._person_download()
        if snapshot_id == COMPANY_SNAPSHOT:
            return HTTPStatus.OK, company_posts()
        return HTTPStatus.NOT_FOUND, {"status": "not_found"}

    def _person_download(self) -> FakeJson:
        match self.person_result:
            case PersonPostScenario.SUCCESS:
                return person_posts(self.metric, self.person_actor_url)
            case PersonPostScenario.ZERO:
                return []
            case PersonPostScenario.NONORIGINAL_ONLY:
                return [person_repost(self.person_actor_url)]
            case PersonPostScenario.OWNERSHIP_CONFLICT:
                return person_posts(self.metric, OWNERSHIP_CANARY)
            case PersonPostScenario.INVALID_SCHEMA:
                raise AssertionError


def credential_label(authorization: str) -> str:
    if authorization == f"Bearer {REVOKED_VALUE}":
        return "revoked"
    if authorization == f"Bearer {ACTIVE_VALUE}":
        return "active"
    return "unknown"
