from __future__ import annotations

from enum import StrEnum
from typing import Final

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
PERSON_SNAPSHOT: Final = "person-snapshot"
COMPANY_SNAPSHOT: Final = "company-snapshot"
PERSON_POST_IDS: Final = (
    "linkedin:post:urn:li:activity:1001",
    "linkedin:post:urn:li:activity:1002",
    "linkedin:post:urn:li:activity:1003",
)
PERSON_FEED_IDS: Final = tuple(reversed(PERSON_POST_IDS))
type FakeJson = dict[str, str] | list[dict[str, str | int | object]]


class PersonPostScenario(StrEnum):
    SUCCESS = "success"
    ZERO = "zero"
    NONORIGINAL_ONLY = "nonoriginal_only"
    OWNERSHIP_CONFLICT = "ownership_conflict"
    INVALID_SCHEMA = "invalid_schema"


def person_posts(metric: int, actor_url: str) -> list[dict[str, str | int | object]]:
    return [
        {
            "id": "urn:li:activity:1001",
            "date_posted": "2026-08-19T09:30:00Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-1001/?trk=x",
            "user_id": "synthetic-provider-person",
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
            "user_id": "synthetic-provider-person",
            "profile_url": actor_url,
            "post_text": "Synthetic second original post",
            "future_field": {"preserved": True},
        },
        {
            "id": "urn:li:activity:1003",
            "date_posted": "2026-08-19T11:30:00Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-ada_example-1003/",
            "user_id": "synthetic-provider-person",
            "profile_url": actor_url,
            "post_text": "Synthetic third original post",
        },
        person_repost(actor_url),
    ]


def person_repost(actor_url: str) -> dict[str, str | int | object]:
    return {
        "id": "urn:li:activity:1004",
        "date_posted": "2026-08-19T12:30:00Z",
        "post_type": "repost",
        "url": "https://www.linkedin.com/posts/synthetic-ada_example-1004/",
        "user_id": "synthetic-provider-person",
        "profile_url": actor_url,
        "post_text": "Synthetic repost retained only as source",
    }


def company_posts() -> list[dict[str, str | int | object]]:
    return [
        {
            "id": "urn:li:activity:2001",
            "date_posted": "2026-08-18T08:00:00Z",
            "post_type": "post",
            "url": "https://www.linkedin.com/posts/synthetic-labs_example-2001/",
            "user_id": "synthetic-provider-company",
            "company_url": COMPANY_URL,
            "post_text": "Synthetic company post",
        }
    ]
