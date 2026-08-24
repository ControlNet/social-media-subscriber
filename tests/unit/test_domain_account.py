from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Final, assert_type

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.domain.platform import AccountKind, Platform

__all__ = ("FIRST_SEEN", "_account")

FIRST_SEEN = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)
_PERSON_URL: Final = "https://www.linkedin.com/in/synthetic-ada/"
_COMPANY_URL: Final = "https://www.linkedin.com/company/synthetic-labs/"


def _account(*, kind: AccountKind = AccountKind.PERSON) -> Account:
    profile_url = _PERSON_URL if kind is AccountKind.PERSON else _COMPANY_URL
    return Account(
        platform=Platform.LINKEDIN,
        kind=kind,
        profile_url=profile_url,
        first_seen_at=FIRST_SEEN,
    )


@pytest.mark.parametrize("kind", tuple(AccountKind))
def test_account_uses_profile_url_as_its_runtime_identity(kind: AccountKind) -> None:
    account = _account(kind=kind)

    assert account.id == account.profile_url
    _ = assert_type(account.id, AccountId)
    assert set(account.model_dump()) == {
        "platform",
        "kind",
        "profile_url",
        "first_seen_at",
    }


@pytest.mark.parametrize(
    "noncanonical_url",
    [
        "https://linkedin.com/in/synthetic-ada/",
        "https://www.linkedin.com/in/synthetic-ada",
        "https://www.linkedin.com/in/synthetic-ada/?tracking=synthetic",
        "https://www.linkedin.com/in/synthetic-ada/#about",
        "https://www.linkedin.com/in/synthetic%2eada/",
    ],
)
def test_account_rejects_noncanonical_profile_url(noncanonical_url: str) -> None:
    values = _account().model_dump()
    values["profile_url"] = noncanonical_url

    with pytest.raises(ValidationError):
        _ = Account.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_seen_at", "2026-08-20T12:30:00"),
        (
            "first_seen_at",
            datetime(2026, 8, 20, 14, 30, tzinfo=timezone(timedelta(hours=2))),
        ),
        ("kind", "group"),
    ],
)
def test_account_rejects_invalid_boundary_values(
    field: str, value: str | datetime
) -> None:
    values = _account().model_dump()
    values[field] = value

    with pytest.raises(ValidationError):
        _ = Account.model_validate(values)


@pytest.mark.parametrize(
    ("kind", "profile_url"),
    [
        (AccountKind.PERSON, _COMPANY_URL),
        (AccountKind.COMPANY, _PERSON_URL),
    ],
)
def test_account_rejects_wrong_kind_url_identity(
    kind: AccountKind, profile_url: str
) -> None:
    values = _account().model_dump()
    values["kind"] = kind
    values["profile_url"] = profile_url

    with pytest.raises(ValidationError) as captured:
        _ = Account.model_validate(values)

    assert captured.value.errors(include_input=False)[0]["type"] == (
        "account_kind_url_mismatch"
    )


@pytest.mark.parametrize(
    "obsolete_field",
    ["id", "schema_version", "platform_account_id", "url_aliases"],
)
def test_account_rejects_obsolete_persisted_identity_fields(
    obsolete_field: str,
) -> None:
    values = _account().model_dump()
    values[obsolete_field] = "obsolete"

    with pytest.raises(ValidationError):
        _ = Account.model_validate(values)
