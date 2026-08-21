from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, cast, final

import pytest
from pydantic import ValidationError

from social_media_subscriber.adapters.instance import AdapterPostRequest
from social_media_subscriber.domain import AccountId, AccountKind
from social_media_subscriber.providers.brightdata.actor_ownership import (
    actor_account_id,
    validate_actor_ownership,
)
from social_media_subscriber.providers.brightdata.adapter_posts import (
    BrightDataPostCollector,
)
from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    JsonValue,
)
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)
from tests.unit.test_brightdata_normalizer_support import account, post_with

if TYPE_CHECKING:
    from social_media_subscriber.providers.brightdata.adapter_contracts import (
        BrightDataClientContract,
    )

_ACTOR_FIELDS = ("use_url", "user_url", "profile_url", "company_url")
_PERSON_URL = "https://www.linkedin.com/in/synthetic-ada/"
_COMPANY_URL = "https://www.linkedin.com/company/synthetic-labs/"


@final
class _SyntheticPostClient:
    def __init__(self, records: tuple[BrightDataPost, ...]) -> None:
        self._records: tuple[BrightDataPost, ...] = records

    async def collect_person_posts(self, _inputs: object) -> tuple[BrightDataPost, ...]:
        return self._records


def _without_actor_urls(**updates: JsonValue) -> dict[str, JsonValue]:
    payload = post_with().payload
    for field in _ACTOR_FIELDS:
        _ = payload.pop(field, None)
    return payload | updates


def test_missing_actor_url_fails_even_when_user_id_is_present() -> None:
    with pytest.raises(ValidationError) as captured:
        _ = BrightDataPost.model_validate(_without_actor_urls(user_id="12345"))

    assert "provider_post_actor" in str(captured.value)
    assert "12345" not in str(captured.value)


def test_ownership_helper_rejects_user_id_only_model_copy() -> None:
    record = post_with(user_id="12345").model_copy(update=dict.fromkeys(_ACTOR_FIELDS))

    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = actor_account_id(record, AccountKind.PERSON)

    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP


@pytest.mark.parametrize("actor_field", _ACTOR_FIELDS)
def test_each_actor_url_field_routes_by_canonical_url_only(actor_field: str) -> None:
    record = BrightDataPost.model_validate(
        _without_actor_urls(
            user_id="EXPLICITLY-IGNORED",
            **{actor_field: "https://uk.linkedin.com/in/synthetic-ada?trk=x#fragment"},
        )
    )

    owner = actor_account_id(record, AccountKind.PERSON)

    assert owner == _PERSON_URL


def test_all_actor_urls_must_agree_after_strict_canonicalization() -> None:
    record = BrightDataPost.model_validate(
        _without_actor_urls(
            use_url="https://linkedin.com/in/synthetic-ada",
            user_url="https://uk.linkedin.com/in/synthetic-ada/?trk=x#fragment",
            profile_url=_PERSON_URL,
            company_url="https://www.linkedin.com/in/synthetic-ada/",
        )
    )

    assert actor_account_id(record, AccountKind.PERSON) == _PERSON_URL


@pytest.mark.parametrize("actor_field", _ACTOR_FIELDS)
@pytest.mark.parametrize(
    "actor_url",
    [
        "not-a-linkedin-locator",
        "https://www.linkedin.com/company/synthetic-labs/",
        "https://www.linkedin.com/in/synthetic-other/",
    ],
)
def test_malformed_wrong_kind_and_cross_owner_actor_urls_fail_closed(
    actor_field: str,
    actor_url: str,
) -> None:
    record = BrightDataPost.model_validate(
        _without_actor_urls(**{actor_field: actor_url})
    )

    with pytest.raises(BrightDataNormalizationError) as captured:
        validate_actor_ownership(account(), record)

    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP
    assert "synthetic-other" not in str(captured.value)
    assert "synthetic-labs" not in str(captured.value)


def test_one_conflicting_actor_url_invalidates_the_entire_record() -> None:
    record = BrightDataPost.model_validate(
        _without_actor_urls(
            use_url=_PERSON_URL,
            profile_url="https://www.linkedin.com/in/synthetic-other/",
        )
    )

    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = actor_account_id(record, AccountKind.PERSON)

    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP


def test_one_malformed_actor_url_invalidates_otherwise_matching_fields() -> None:
    record = BrightDataPost.model_validate(
        _without_actor_urls(
            use_url=_PERSON_URL,
            user_url="not-a-linkedin-locator",
        )
    )

    with pytest.raises(BrightDataNormalizationError) as captured:
        _ = actor_account_id(record, AccountKind.PERSON)

    assert captured.value.category is BrightDataNormalizationErrorCategory.OWNERSHIP


def test_provider_user_id_is_typed_payload_data_not_ownership() -> None:
    first = post_with(user_id="first-provider-value")
    second = post_with(user_id="different-provider-value")

    assert actor_account_id(first, AccountKind.PERSON) == _PERSON_URL
    assert actor_account_id(second, AccountKind.PERSON) == _PERSON_URL


def test_company_actor_url_matches_company_request() -> None:
    record = BrightDataPost.model_validate(
        _without_actor_urls(company_url=_COMPANY_URL)
    )

    validate_actor_ownership(account(kind=AccountKind.COMPANY), record)
    assert actor_account_id(record, AccountKind.COMPANY) == _COMPANY_URL


@pytest.mark.anyio
async def test_collector_routes_records_by_actor_url_not_provider_user_id() -> None:
    other_url = "https://www.linkedin.com/in/synthetic-grace/"
    first_account = account()
    second_account = account().model_copy(
        update={"id": AccountId(other_url), "profile_url": other_url}
    )
    first_record = post_with(user_id="points-to-second")
    second_record = post_with(
        id="urn:li:activity:1002",
        user_id="points-to-first",
        use_url=other_url,
        url="https://www.linkedin.com/posts/synthetic-grace_example-1002/",
    )
    client = cast(
        "BrightDataClientContract",
        cast("object", _SyntheticPostClient((second_record, first_record))),
    )
    collector = BrightDataPostCollector(client, first_account.first_seen_at)
    window = (date(2026, 8, 1), date(2026, 8, 21))

    result = await collector.collect(
        (
            AdapterPostRequest(first_account, *window),
            AdapterPostRequest(second_account, *window),
        )
    )

    assert tuple(item.account_id for item in result.accounts) == (
        first_account.id,
        second_account.id,
    )
    assert tuple(item.posts[0].platform_post_id for item in result.accounts) == (
        "urn:li:activity:1001",
        "urn:li:activity:1002",
    )
