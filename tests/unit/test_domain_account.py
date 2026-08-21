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
        id=AccountId(profile_url),
        platform=Platform.LINKEDIN,
        kind=kind,
        profile_url=profile_url,
        first_seen_at=FIRST_SEEN,
    )


@pytest.mark.parametrize("kind", tuple(AccountKind))
def test_account_canonical_url_identity_is_the_profile_url(kind: AccountKind) -> None:
    # Given / When
    account = _account(kind=kind)

    # Then
    assert account.id == account.profile_url
    _ = assert_type(account.id, AccountId)


@pytest.mark.parametrize("kind", tuple(AccountKind))
def test_account_round_trip_preserves_schema_v2_url_identity(kind: AccountKind) -> None:
    # Given
    account = _account(kind=kind)

    # When
    restored = Account.model_validate_json(account.model_dump_json())

    # Then
    assert restored == account
    assert restored.schema_version == 2
    assert set(restored.model_dump()) == {
        "schema_version",
        "id",
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
        "https://www.linkedin.com/in/synthetic%2eada/",
    ],
)
def test_account_rejects_noncanonical_url_identity(noncanonical_url: str) -> None:
    # Given
    values = _account().model_dump()
    values["id"] = noncanonical_url
    values["profile_url"] = noncanonical_url

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Account.model_validate(values)

    # Then
    assert captured.value.error_count() >= 1


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
    # Given
    values = _account().model_dump()
    values[field] = value

    # When / Then
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
    # Given
    values = _account().model_dump()
    values["id"] = profile_url
    values["kind"] = kind
    values["profile_url"] = profile_url

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Account.model_validate(values)

    # Then
    assert captured.value.errors(include_input=False)[0]["type"] == (
        "account_kind_url_mismatch"
    )


def test_account_rejects_mismatched_canonical_profile_url() -> None:
    # Given
    values = _account().model_dump()
    values["profile_url"] = "https://www.linkedin.com/in/synthetic-grace/"

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Account.model_validate(values)

    # Then
    assert captured.value.errors(include_input=False)[0]["type"] == (
        "account_id_mismatch"
    )


@pytest.mark.parametrize(
    "legacy_updates",
    [
        {"id": "linkedin:person:12345"},
        {"platform_account_id": "12345"},
        {"url_aliases": (_PERSON_URL,)},
        {"schema_version": 1},
    ],
)
def test_account_rejects_legacy_numeric_or_alias_identity(
    legacy_updates: dict[str, str | int | tuple[str, ...]],
) -> None:
    # Given
    values = _account().model_dump()
    values.update(legacy_updates)

    # When
    with pytest.raises(ValidationError) as captured:
        _ = Account.model_validate(values)

    # Then
    assert captured.value.error_count() >= 1


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
