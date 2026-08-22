from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from social_media_subscriber.domain import Account, AccountId, AccountKind, Platform
from social_media_subscriber.domain.ids import (
    PlatformPostId,
    post_id_for,
    record_filename,
)
from social_media_subscriber.domain.post import Post, PostKind, StablePostContent
from social_media_subscriber.providers.brightdata.models import (
    BrightDataPost,
    JsonValue,
)
from social_media_subscriber.providers.brightdata.source_record import (
    BrightDataLinkedInPostSourceRecord,
)
from social_media_subscriber.serialization.json import (
    JsonBoundaryModel,
    canonical_json_bytes,
    canonical_json_value_bytes,
)
from social_media_subscriber.storage.layout import MANIFEST, snapshot_digest
from social_media_subscriber.storage.repository import (
    SnapshotIntegrityError,
    SnapshotRepository,
)
from social_media_subscriber.storage.snapshot import SnapshotManifest, SnapshotState

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
ACCOUNT_URL = "https://www.linkedin.com/in/synthetic-ada/"
SENSITIVE_CANARY = "EXPLICIT_NEGATIVE_TEST_CREDENTIAL_CANARY"
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def _state() -> SnapshotState:
    account = Account(
        schema_version=2,
        id=AccountId(ACCOUNT_URL),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=ACCOUNT_URL,
        first_seen_at=NOW,
    )
    platform_post_id = PlatformPostId("urn:li:activity:1001")
    post = Post.from_stable(
        StablePostContent(
            schema_version=2,
            id=post_id_for(platform_post_id),
            platform_post_id=platform_post_id,
            account_id=account.id,
            canonical_url="https://www.linkedin.com/posts/synthetic-1001/",
            published_at=NOW,
            text="Synthetic",
            kind=PostKind.ORIGINAL,
            hashtags=(),
            links=(),
        ),
        NOW,
    )
    provider = BrightDataPost.model_validate(
        {
            "id": str(platform_post_id),
            "date_posted": "2026-08-20T12:00:00+00:00",
            "post_type": "post",
            "url": post.canonical_url,
            "use_url": ACCOUNT_URL,
            "num_likes": 1,
        }
    )
    source = BrightDataLinkedInPostSourceRecord.from_post(account.id, provider)
    return SnapshotState((account,), (post,), (source,))


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _refresh_manifest_digest(root: Path) -> None:
    non_manifest = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root) != MANIFEST
    }
    manifest = SnapshotManifest.model_validate_json((root / MANIFEST).read_bytes())
    _ = (root / MANIFEST).write_bytes(
        canonical_json_bytes(
            manifest.model_copy(update={"digest": snapshot_digest(non_manifest)})
        )
    )


def storage_state() -> SnapshotState:
    return _state()


def tree_bytes(root: Path) -> dict[str, bytes]:
    return _tree(root)


def test_repository_writes_deterministic_v2_url_owned_tree(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    state = _state()

    manifest = SnapshotRepository(root).write(state)
    loaded = SnapshotRepository(root).load_optional()

    assert loaded == state
    assert manifest.account_count == manifest.post_count == 1
    assert manifest.source_record_count == 1
    assert set(_tree(root)) == {
        f"accounts/{record_filename(state.accounts[0].id)}",
        f"posts/linkedin/{record_filename(state.posts[0].id)}",
        f"source/brightdata/linkedin/posts/{record_filename(state.posts[0].id)}",
        "accounts.json",
        "feed.json",
        "snapshot.json",
    }


def test_repository_repeated_write_is_byte_identical(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    state = _state()
    _ = SnapshotRepository(root).write(state)
    before = _tree(root)

    _ = SnapshotRepository(root).write(state)

    assert _tree(root) == before


def test_repository_replaces_an_empty_output_placeholder(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    root.mkdir()
    state = _state()

    _ = SnapshotRepository(root).write(state)

    assert SnapshotRepository(root).load_optional() == state


@pytest.mark.parametrize("target", ["snapshot.json", "accounts.json", "feed.json"])
def test_repository_rejects_corrupt_inventory(tmp_path: Path, target: str) -> None:
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(_state())
    _ = (root / target).write_bytes(b"{}\n")

    with pytest.raises(SnapshotIntegrityError):
        _ = SnapshotRepository(root).load_optional()


@pytest.mark.parametrize(
    "record_glob",
    [
        "accounts/*.json",
        "posts/linkedin/*.json",
        "source/brightdata/linkedin/posts/*.json",
    ],
    ids=("account", "post", "source"),
)
def test_repository_rejects_legacy_v1_records(tmp_path: Path, record_glob: str) -> None:
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(_state())
    record = next(root.glob(record_glob))
    payload = _JSON_OBJECT.validate_json(record.read_bytes())
    payload["schema_version"] = 1
    if record_glob.startswith("accounts"):
        payload.update(
            {
                "id": "linkedin:person:101",
                "platform_account_id": "101",
                "url_aliases": [ACCOUNT_URL],
            }
        )
    _ = record.write_bytes(_JSON_OBJECT.dump_json(payload))
    _refresh_manifest_digest(root)

    with pytest.raises(SnapshotIntegrityError):
        _ = SnapshotRepository(root).load_optional()


@pytest.mark.parametrize(
    "payload_update",
    [
        {"snapshotId": SENSITIVE_CANARY},
        {"provider_metadata": {"snapshot_id": SENSITIVE_CANARY}},
        {
            "provider_metadata": {
                "request": {"headers": {"authorization": SENSITIVE_CANARY}}
            }
        },
        {"provider_metadata": {"requestHeader": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"request_header": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"request-header": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"requestHeaders": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"request_headers": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"request-headers": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"responseHeader": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"response_header": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"response-header": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"responseHeaders": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"response_headers": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"response-headers": {"x-synthetic": SENSITIVE_CANARY}}},
        {"provider_metadata": {"requestAuth": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"REQUEST_AUTH": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"request-auth": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"requestCookie": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"REQUEST_COOKIE": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"request-cookie": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"requestCredentials": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"REQUEST_CREDENTIALS": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"request-credentials": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"clientRequestHeaders": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"CLIENT_REQUEST_HEADERS": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"client-request-headers": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"providerAuthInfo": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"providerBearerInfo": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"PROVIDER_BEARER_INFO": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"provider-bearer-info": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"PROVIDER_AUTH_INFO": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"provider-auth-info": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"responseAuthentication": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"RESPONSE_AUTHENTICATION": {"value": SENSITIVE_CANARY}}},
        {"provider_metadata": {"response-authentication": {"value": SENSITIVE_CANARY}}},
    ],
)
def test_repository_rejects_rehashed_sensitive_source_payload_without_canary_leak(
    tmp_path: Path,
    payload_update: dict[str, JsonValue],
) -> None:
    # Given
    root = tmp_path / "dist"
    _ = SnapshotRepository(root).write(_state())
    record = next(root.glob("source/brightdata/linkedin/posts/*.json"))
    source = _JSON_OBJECT.validate_json(record.read_bytes())
    payload = _JSON_OBJECT.validate_python(source["payload"])
    payload.update(payload_update)
    source["payload"] = payload
    source["payload_sha256"] = hashlib.sha256(
        canonical_json_value_bytes(payload)
    ).hexdigest()
    _ = record.write_bytes(canonical_json_value_bytes(source))
    _refresh_manifest_digest(root)

    # When / Then
    with pytest.raises(SnapshotIntegrityError) as captured:
        _ = SnapshotRepository(root).load_optional()
    diagnostic = f"{captured.value!s} {captured.value!r}"
    assert SENSITIVE_CANARY not in diagnostic
    assert "snapshot_id" not in diagnostic
    assert "snapshotId" not in diagnostic
    assert "authorization" not in diagnostic


def test_failed_candidate_encoding_preserves_prior_bytes(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    state = _state()
    _ = SnapshotRepository(root).write(state)
    before = _tree(root)
    calls = 0

    def fail_after_first(model: JsonBoundaryModel) -> bytes:
        nonlocal calls
        calls += 1
        if calls > 1:
            reason = "synthetic encoder failure"
            raise RuntimeError(reason)
        return canonical_json_bytes(model)

    with pytest.raises(SnapshotIntegrityError):
        _ = SnapshotRepository(root, fail_after_first).write(state)

    assert _tree(root) == before
