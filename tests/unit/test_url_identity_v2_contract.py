from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator
from social_media_subscriber.adapters.instance import (
    AcceptedSnapshotBatchFailure,
    AccountRejectionCategory,
    AdapterBatch,
    AdapterPostRequest,
    BatchCompleted,
    InvalidCredentialBatchFailure,
    QuotaBatchFailure,
    RejectedAccount,
    RetryableBatchFailure,
    SchemaBatchFailure,
)
from social_media_subscriber.application.results import (
    CandidateChange,
    CollectionExitCode,
    CollectionResult,
)
from social_media_subscriber.cli import create_app
from social_media_subscriber.domain import Account, AccountKind, Platform
from social_media_subscriber.domain.ids import AccountId
from social_media_subscriber.providers.brightdata.adapter_error_mapping import (
    map_provider_error,
)
from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.publishing.git import Published, PublishResult
from social_media_subscriber.storage.layout import snapshot_digest
from social_media_subscriber.storage.snapshot import SnapshotManifest

if TYPE_CHECKING:
    from social_media_subscriber.application.collect import CollectionRequest
    from social_media_subscriber.cli_application import PublicationCommand

_REPORT_ADAPTER: Final = TypeAdapter(dict[str, str | int | list[str] | None])
_SYNTHETIC_PERSON_URL: Final = "https://www.linkedin.com/in/synthetic-ada/"
_SYNTHETIC_COMPANY_URL: Final = "https://www.linkedin.com/company/synthetic-labs/"


@dataclass(frozen=True, slots=True)
class _SyntheticCliApplication:
    result: CollectionResult

    def collect(self, request: CollectionRequest) -> CollectionResult:
        _ = request
        return self.result

    def verify(self, snapshot: Path) -> SnapshotManifest:
        _ = snapshot
        return SnapshotManifest(
            account_count=0,
            post_count=0,
            source_record_count=0,
            digest="0" * 64,
        )

    def publish(self, command: PublicationCommand) -> PublishResult:
        _ = command
        return Published("0" * 40)


def _synthetic_account() -> Account:
    return Account(
        schema_version=2,
        id=AccountId(_SYNTHETIC_PERSON_URL),
        platform=Platform.LINKEDIN,
        kind=AccountKind.PERSON,
        profile_url=_SYNTHETIC_PERSON_URL,
        first_seen_at=datetime(2026, 8, 21, tzinfo=UTC),
    )


def test_account_contract_uses_only_schema_v2_canonical_url_identity() -> None:
    # Given / When
    account = _synthetic_account()
    public_record = account.model_dump(mode="json")

    # Then
    assert public_record == {
        "schema_version": 2,
        "id": _SYNTHETIC_PERSON_URL,
        "platform": "linkedin",
        "kind": "person",
        "profile_url": _SYNTHETIC_PERSON_URL,
        "first_seen_at": "2026-08-21T00:00:00Z",
    }


def _synthetic_batch() -> AdapterBatch:
    return AdapterBatch(
        (
            AdapterPostRequest(
                _synthetic_account(),
                date(2026, 8, 20),
                date(2026, 8, 21),
            ),
        )
    )


@pytest.mark.parametrize(
    ("raw", "expected_kind", "expected_url"),
    [
        (
            "https://linkedin.com/in/Synthetic-Ada/?campaign=synthetic#bio",
            AccountKind.PERSON,
            "https://www.linkedin.com/in/Synthetic-Ada/",
        ),
        (
            "https://ca.linkedin.com/company/synthetic-labs/?campaign=synthetic#about",
            AccountKind.COMPANY,
            _SYNTHETIC_COMPANY_URL,
        ),
    ],
)
def test_locator_canonicalizes_public_person_and_company_urls_when_valid(
    raw: str,
    expected_kind: AccountKind,
    expected_url: str,
) -> None:
    # Given
    synthetic_url = raw

    # When
    locator = parse_linkedin_locator(synthetic_url)

    # Then
    assert locator.platform is Platform.LINKEDIN
    assert locator.kind is expected_kind
    assert locator.canonical_url == expected_url


@pytest.mark.parametrize(
    "raw",
    [
        "http://www.linkedin.com/in/synthetic-ada/",
        "https://example.test/in/synthetic-ada/",
        "https://www.linkedin.com/in/",
        "https://www.linkedin.com/company/synthetic-labs/extra/",
        "https://www.linkedin.com/in/synthetic%2eada/",
    ],
)
def test_locator_rejects_malformed_public_urls_when_parsed(raw: str) -> None:
    # Given
    malformed_url = raw

    # When
    with pytest.raises(AccountInputError) as error:
        _ = parse_linkedin_locator(malformed_url)

    # Then
    assert error.value.category.value == "invalid_account_url"


def test_digest_is_stable_for_sorted_paths_and_exact_file_bytes() -> None:
    # Given
    files = {
        Path("posts/linkedin/synthetic-b.json"): b"second synthetic payload",
        Path("accounts/synthetic-a.json"): b"first synthetic payload",
    }

    # When
    digest = snapshot_digest(files)

    # Then
    assert digest == "e9264f740fba63e9b283e65c6f88b345b2e32761c1db2d639f356496b853678b"


def test_manifest_exposes_the_current_versioned_count_and_digest_keys() -> None:
    # Given
    manifest = SnapshotManifest(
        account_count=1,
        post_count=2,
        source_record_count=3,
        digest="a" * 64,
    )

    # When
    public_manifest = manifest.model_dump()

    # Then
    assert public_manifest == {
        "schema_version": 1,
        "account_count": 1,
        "post_count": 2,
        "source_record_count": 3,
        "digest": "a" * 64,
    }


@pytest.mark.parametrize(
    ("category", "expected_type"),
    [
        (BrightDataErrorCategory.AUTH, InvalidCredentialBatchFailure),
        (BrightDataErrorCategory.QUOTA, QuotaBatchFailure),
        (BrightDataErrorCategory.RETRYABLE, RetryableBatchFailure),
        (BrightDataErrorCategory.TIMEOUT, RetryableBatchFailure),
        (BrightDataErrorCategory.SNAPSHOT_TIMEOUT, SchemaBatchFailure),
        (BrightDataErrorCategory.SNAPSHOT_TERMINAL, SchemaBatchFailure),
        (BrightDataErrorCategory.SCHEMA, SchemaBatchFailure),
    ],
)
def test_failure_taxonomy_maps_terminal_categories_to_typed_router_outcomes(
    category: BrightDataErrorCategory,
    expected_type: type[
        InvalidCredentialBatchFailure
        | QuotaBatchFailure
        | RetryableBatchFailure
        | SchemaBatchFailure
    ],
) -> None:
    # Given
    batch = _synthetic_batch()
    error = BrightDataError(category)

    # When
    outcome = map_provider_error(batch, error)

    # Then
    assert type(outcome) is expected_type


@pytest.mark.parametrize(
    ("category", "expected_rejection"),
    [
        (BrightDataErrorCategory.NOT_FOUND, AccountRejectionCategory.NOT_FOUND),
        (BrightDataErrorCategory.INPUT, AccountRejectionCategory.INVALID),
    ],
)
def test_failure_taxonomy_preserves_typed_account_rejection_categories(
    category: BrightDataErrorCategory,
    expected_rejection: AccountRejectionCategory,
) -> None:
    # Given
    batch = _synthetic_batch()
    error = BrightDataError(category)

    # When
    outcome = map_provider_error(batch, error)

    # Then
    match outcome:
        case BatchCompleted(
            outcomes=(RejectedAccount(account_id=account_id, category=rejection),)
        ):
            assert account_id == batch.accounts[0].id
            assert rejection is expected_rejection
        case unexpected:
            pytest.fail(f"unexpected typed provider outcome: {unexpected!r}")


def test_failure_taxonomy_preserves_accepted_snapshot_precedence() -> None:
    # Given
    batch = _synthetic_batch()
    error = BrightDataError(BrightDataErrorCategory.AUTH, snapshot_accepted=True)

    # When
    outcome = map_provider_error(batch, error)

    # Then
    assert type(outcome) is AcceptedSnapshotBatchFailure


def test_report_keys_are_exact_for_a_successful_collect_command() -> None:
    # Given
    application = _SyntheticCliApplication(
        CollectionResult(
            CollectionExitCode.SUCCESS,
            CandidateChange.CHANGED,
            "a" * 64,
            2,
            0,
            (),
        )
    )

    # When
    result = CliRunner().invoke(
        create_app(application),
        ["collect", "--previous-snapshot", "prior", "--output", "candidate"],
        env={
            "ACCOUNTS": _SYNTHETIC_PERSON_URL,
            "BRIGHT_DATA_API_KEYS": "synthetic-test-credential",
        },
    )
    report_lines = [line for line in result.output.splitlines() if line.startswith("{")]
    report = _REPORT_ADAPTER.validate_json(report_lines[0])

    # Then
    assert result.exit_code == 0
    assert len(report_lines) == 1
    assert set(report) == {
        "candidate_change",
        "command",
        "digest",
        "exit_code",
        "failed_account_ids",
        "failed_accounts",
        "succeeded_accounts",
    }
    assert report == {
        "candidate_change": "changed",
        "command": "collect",
        "digest": "a" * 64,
        "exit_code": 0,
        "failed_account_ids": [],
        "failed_accounts": 0,
        "succeeded_accounts": 2,
    }
