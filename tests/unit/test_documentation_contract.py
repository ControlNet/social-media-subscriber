from __future__ import annotations

from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).parents[2]
README: Final = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
ARCHITECTURE: Final = (PROJECT_ROOT / "docs/architecture.md").read_text(
    encoding="utf-8"
)
OPERATIONS: Final = (PROJECT_ROOT / "docs/operations.md").read_text(encoding="utf-8")
ALL_IDENTITY_DOCS: Final = f"{README}\n{ARCHITECTURE}\n{OPERATIONS}"
NORMALIZED_IDENTITY_DOCS: Final = " ".join(ALL_IDENTITY_DOCS.replace("`", "").split())


def test_url_identity_docs_define_the_exact_persisted_identity() -> None:
    required_contract = (
        "Account.id == Account.profile_url",
        "Post.account_id",
        "Bright Data source-record account_id",
        "distinct Account",
        "use_url, user_url, profile_url, and company_url",
        "every supplied actor URL",
    )

    for statement in required_contract:
        assert statement in NORMALIZED_IDENTITY_DOCS


def test_url_identity_docs_define_v2_outcomes_and_atomicity() -> None:
    required_contract = (
        "schema_version: 2",
        "v1 records and snapshots are rejected",
        "successful response with zero records",
        "typed NOT_FOUND",
        "whole candidate",
        "prior snapshot remains byte-identical",
        "failed_account_ids",
        "canonical requested LinkedIn URLs",
    )

    for statement in required_contract:
        assert statement in NORMALIZED_IDENTITY_DOCS


def test_url_identity_docs_reject_legacy_and_unauthorized_promises() -> None:
    required_boundaries = (
        "user_id is optional provider payload data only",
        "No migration or compatibility reader is provided",
        "Alias reconciliation and entity merging are not supported",
        "does not authorize a live provider call",
        "does not authorize publication",
        "does not perform a remote cutover",
    )
    forbidden_promises = (
        "automatically migrate",
        "backward compatible",
        "merge changed slugs",
        "run publish-dist to test",
        "call Bright Data to verify",
    )
    stale_positive_claims = (
        "numeric platform_account_id, stable profile_url, sorted unique url_aliases",
        "Known aliases resolve locally",
        "All three formats currently have schema_version: 1",
    )

    for statement in required_boundaries:
        assert statement in NORMALIZED_IDENTITY_DOCS
    for statement in (*forbidden_promises, *stale_positive_claims):
        assert statement not in NORMALIZED_IDENTITY_DOCS


def test_url_identity_docs_publish_copy_pastable_offline_pixi_checks() -> None:
    offline_commands = (
        (
            "pixi run test tests/unit/test_documentation_contract.py "
            "-k url_identity_docs -q"
        ),
        "pixi run schemas-check",
        "pixi run verify",
    )

    for command in offline_commands:
        assert command in OPERATIONS
