from __future__ import annotations

__test__ = False

from datetime import UTC, datetime, timedelta, timezone
from typing import assert_type

import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.ids import (
    AccountId,
    InvalidPlatformAccountIdError,
    PlatformAccountId,
    account_id_for,
)
from social_media_subscriber.domain.platform import AccountKind, Platform

__all__ = ("FIRST_SEEN", "_account")

FIRST_SEEN = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)
MALFORMED_PLATFORM_ACCOUNT_IDS = (
    "",
    "abc",
    "../../x",
    "urn:li:person:123",
    "12/34",
    "12.34",
    "+123",
    " 123",
    "123 ",
    "123\n",
    "\uff11\uff12\uff13",
)
UNSAFE_ACCOUNT_URLS = [
    "https://www.linkedin.com/in/syn\rthetic/",
    "https://www.linkedin.com/in/syn\nthetic/",
    "https://www.linkedin.com/in/syn\tthetic/",
    "https://www.linkedin.com/in/syn\x7fthetic/",
    "https://www.linkedin.com/in/syn\\thetic/",
    "https://www.linkedin.com/in/syn%2fthetic/",
    "https://www.linkedin.com/in/syn%2Fthetic/",
    "https://www.linkedin.com/in/syn%5cthetic/",
    "https://www.linkedin.com/in/syn%5Cthetic/",
    "https://www.linkedin.com/in/syn%2ethetic/",
    "https://www.linkedin.com/in/syn%2Ethetic/",
    "https://www.linkedin.com/in/%2e%2E/",
    "https://www.linkedin.com/in/syn%09thetic/",
    "https://www.linkedin.com/in/syn%0athetiC/",
    "https://www.linkedin.com/in/syn%0Athetic/",
    "https://www.linkedin.com/in/syn%0dthetic/",
    "https://www.linkedin.com/in/syn%0Dthetic/",
    "https://www.linkedin.com/in/syn%7fthetic/",
    "https://www.linkedin.com/in/syn%7Fthetic/",
    "https://www.linkedin.com/in/./",
    "https://www.linkedin.com/in/../",
]


def _account(*, kind: AccountKind = AccountKind.PERSON) -> Account:
    platform_account_id = PlatformAccountId("12345")
    return Account(
        id=account_id_for(kind, platform_account_id),
        platform=Platform.LINKEDIN,
        kind=kind,
        platform_account_id=platform_account_id,
        profile_url="https://www.linkedin.com/in/ada/",
        url_aliases=(
            "https://www.linkedin.com/in/ada-lovelace/",
            "https://www.linkedin.com/in/ada/",
            "https://www.linkedin.com/in/ada-lovelace/",
        ),
        first_seen_at=FIRST_SEEN,
    )


def test_account_normalizes_order_and_preserves_type_brands() -> None:
    # Given / When
    account = _account()

    # Then
    assert account.url_aliases == (
        "https://www.linkedin.com/in/ada-lovelace/",
        "https://www.linkedin.com/in/ada/",
    )
    assert account.first_seen_at is FIRST_SEEN
    _ = assert_type(account.id, AccountId)
    _ = assert_type(account.platform_account_id, PlatformAccountId)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_seen_at", "2026-08-20T12:30:00"),
        (
            "first_seen_at",
            datetime(2026, 8, 20, 14, 30, tzinfo=timezone(timedelta(hours=2))),
        ),
        ("id", "linkedin:company:12345"),
        ("profile_url", "https://user@www.linkedin.com/in/ada/"),
        ("profile_url", "http://www.linkedin.com/in/ada/"),
        ("profile_url", "https://www.linkedin.com/company/ada/"),
        ("kind", "group"),
    ],
)
def test_account_rejects_invalid_boundary_values(
    field: str, value: str | datetime
) -> None:
    # Given
    values = _account().model_dump()
    values[field] = value

    # When / Then
    with pytest.raises(ValidationError):
        _ = Account.model_validate(values)


@pytest.mark.parametrize("kind", tuple(AccountKind))
@pytest.mark.parametrize("malformed_id", MALFORMED_PLATFORM_ACCOUNT_IDS)
def test_account_id_constructor_rejects_non_ascii_numeric_platform_id(
    kind: AccountKind,
    malformed_id: str,
) -> None:
    # Given
    platform_account_id = PlatformAccountId(malformed_id)

    # When / Then
    with pytest.raises(InvalidPlatformAccountIdError):
        _ = account_id_for(kind, platform_account_id)


@pytest.mark.parametrize("kind", tuple(AccountKind))
@pytest.mark.parametrize("malformed_id", MALFORMED_PLATFORM_ACCOUNT_IDS)
def test_account_boundary_rejects_non_ascii_numeric_platform_id(
    kind: AccountKind,
    malformed_id: str,
) -> None:
    # Given
    kind_path = {
        AccountKind.PERSON: "in",
        AccountKind.COMPANY: "company",
    }[kind]
    values = {
        "id": f"linkedin:{kind.value}:{malformed_id}",
        "platform": Platform.LINKEDIN,
        "kind": kind,
        "platform_account_id": malformed_id,
        "profile_url": f"https://www.linkedin.com/{kind_path}/synthetic/",
        "url_aliases": (f"https://www.linkedin.com/{kind_path}/synthetic/",),
        "first_seen_at": FIRST_SEEN,
    }

    # When / Then
    with pytest.raises(ValidationError):
        _ = Account.model_validate(values)


@pytest.mark.parametrize("field", ["profile_url", "url_aliases"])
@pytest.mark.parametrize("unsafe_url", UNSAFE_ACCOUNT_URLS)
def test_account_boundary_rejects_unsafe_encoded_or_control_profile_url(
    field: str,
    unsafe_url: str,
) -> None:
    # Given
    values = _account().model_dump()
    values[field] = (unsafe_url,) if field == "url_aliases" else unsafe_url

    # When / Then
    with pytest.raises(ValidationError):
        _ = Account.model_validate(values)


def test_account_boundary_error_representations_redact_invalid_id_input() -> None:
    # Given
    canary = "invalid-account-id-canary-9f2c6d"
    values = _account().model_dump()
    values["id"] = f"linkedin:person:{canary}"
    values["platform_account_id"] = canary

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Account.model_validate(values)

    # Then
    error = captured.value
    public_representations = (
        str(error),
        repr(error),
        error.json(),
        repr(error.errors()),
        repr(error.args),
    )
    assert all(
        canary not in representation for representation in public_representations
    )
    assert error.error_count() >= 1
    assert error.title == "Account"
    details = error.errors(include_input=False)
    assert {detail["loc"] for detail in details} == {
        ("id",),
        ("platform_account_id",),
    }
    assert {detail["type"] for detail in details} == {"string_pattern_mismatch"}


def test_account_boundary_error_representations_redact_invalid_account_id() -> None:
    # Given
    canary = "invalid-canonical-account-id-canary-82d1"
    values = _account().model_dump()
    values["id"] = canary

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Account.model_validate(values)

    # Then
    error = captured.value
    public_representations = (
        str(error),
        repr(error),
        error.json(),
        repr(error.errors()),
        repr(error.args),
    )
    assert all(
        canary not in representation for representation in public_representations
    )
    assert error.errors(include_input=False)[0]["loc"] == ("id",)
