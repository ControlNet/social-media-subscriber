from __future__ import annotations

import json
from datetime import UTC, datetime

from social_media_subscriber.domain.account import Account
from social_media_subscriber.domain.platform import AccountKind, Platform
from social_media_subscriber.serialization.json import canonical_json_bytes


def _account() -> Account:
    profile_url = "https://www.linkedin.com/in/synthetic-ada/"
    return Account(
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=profile_url,
        first_seen_at=datetime(2026, 8, 20, 12, 30, tzinfo=UTC),
    )


def test_canonical_json_is_utf8_sorted_indented_lf_terminated_and_deterministic() -> (
    None
):
    # Given
    ordered = _account()
    shuffled_fields = Account.model_validate(
        dict(reversed(tuple(ordered.model_dump().items())))
    )

    # When
    first = canonical_json_bytes(ordered)
    second = canonical_json_bytes(shuffled_fields)

    # Then
    assert first == second
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")
    assert b"\r" not in first
    assert first.decode("utf-8").startswith('{\n  "first_seen_at"')
    assert json.loads(first) == ordered.model_dump(mode="json")
