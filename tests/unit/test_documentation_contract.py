from __future__ import annotations

from pathlib import Path
from typing import Final

PROJECT_ROOT: Final = Path(__file__).parents[2]
README: Final = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
ARCHITECTURE: Final = (PROJECT_ROOT / "docs/architecture.md").read_text(
    encoding="utf-8"
)
OPERATIONS: Final = (PROJECT_ROOT / "docs/operations.md").read_text(encoding="utf-8")
ALL_IDENTITY_DOCS: Final = f"{ARCHITECTURE}\n{OPERATIONS}"
NORMALIZED_IDENTITY_DOCS: Final = " ".join(ALL_IDENTITY_DOCS.replace("`", "").split())


def test_readme_has_docker_run_example_with_persistent_volumes() -> None:
    for command in (
        "docker run -d",
        "--env ACCOUNTS=",
        "--env SOURCES=",
        '--volume "$PWD/social-media:/data"',
        '--volume "$PWD/state:/state"',
        "controlnet/social-media-subscriber:latest",
    ):
        assert command in README
    assert "provider charges" in README
    assert "Both accept commas, newlines" in README
    assert "--platform linux/amd64" not in README
    assert "--env-file" not in README


def test_readme_focuses_on_features_and_usage() -> None:
    for heading in (
        "## Features",
        "## Configuration",
        "## Docker Compose",
        "## Docker",
        "## GitHub Actions",
        "## Local use",
        "## Documentation",
    ):
        assert heading in README
    for detail in (
        "## Exit contract",
        "## Persisted data boundary",
        "profileReplies",
        "Account.id",
    ):
        assert detail not in README
    for document in (
        "docs/operations.md",
        "docs/architecture.md",
        "docs/media-and-docker.md",
    ):
        assert document in README


def test_url_identity_docs_define_the_exact_persisted_identity() -> None:
    required_contract = (
        "profile_url is the only persisted Account identity",
        "Post.account_profile_url",
        "distinct Account",
        "use_url, user_url, profile_url, and company_url",
        "every supplied actor URL",
    )

    for statement in required_contract:
        assert statement in NORMALIZED_IDENTITY_DOCS


def test_persisted_data_docs_define_outcomes_and_atomicity() -> None:
    required_contract = (
        "successful response with zero records",
        "Reply, repost, quote, media-only",
        "does not contain a feed",
        "posts.json",
        "newest-first",
        "path, account_profile_url, published_at, and platform",
        "typed NOT_FOUND",
        "whole candidate",
        "prior snapshot remains byte-identical",
        "failed_account_ids",
        "canonical requested LinkedIn URLs",
    )

    for statement in required_contract:
        assert statement in NORMALIZED_IDENTITY_DOCS


def test_brightdata_docs_do_not_promise_complete_profile_history() -> None:
    required_boundaries = (
        "does not guarantee complete profile history",
        "not a completeness guarantee",
        "narrower date windows do not establish completeness",
    )

    for statement in required_boundaries:
        assert statement in NORMALIZED_IDENTITY_DOCS


def test_url_identity_docs_reject_legacy_and_unauthorized_promises() -> None:
    required_boundaries = (
        "user_id is optional provider payload data only",
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
        "schema_version",
    )

    for statement in required_boundaries:
        assert statement in NORMALIZED_IDENTITY_DOCS
    for statement in (*forbidden_promises, *stale_positive_claims):
        assert statement not in NORMALIZED_IDENTITY_DOCS


def test_persisted_data_docs_publish_copy_pastable_offline_pixi_checks() -> None:
    offline_commands = (
        (
            "pixi run test tests/unit/test_documentation_contract.py "
            "-k persisted_data_docs -q"
        ),
        "pixi run schemas-check",
        "pixi run verify",
    )

    for command in offline_commands:
        assert command in OPERATIONS


def test_source_configuration_docs_define_ordered_allowlisted_composition() -> None:
    required_contract = (
        "ACCOUNTS and SOURCES",
        "<source_id>:<api_token>",
        "splits only the first colon",
        "current allowlist contains apify and brightdata",
        "always tries every configured Apify credential before Bright Data",
        "Line order is preserved within each provider",
        "another independent fallback instance",
        "never creates a Cartesian product",
    )

    for statement in required_contract:
        assert statement in NORMALIZED_IDENTITY_DOCS


def test_apify_docs_define_window_and_failover_boundaries() -> None:
    required_contract = (
        "postedLimitDate",
        "query.targetUrl",
        "never persist that Actor request metadata",
        "a repost can identify its original author",
        "disables comments and reactions",
        "enforces the complete inclusive date window locally",
        "maxPosts=0 as the Actor's unlimited sentinel",
        "omitting maxPosts activates the provider default",
        "without a total item limit",
        "does not fail over to another token or provider",
    )

    for statement in required_contract:
        assert statement in NORMALIZED_IDENTITY_DOCS


def test_linkedin_post_docs_define_cross_provider_canonicalization() -> None:
    required_contract = (
        "whole-second precision",
        "positive repost evidence",
        "feed/update/urn:li:activity:<id>",
        "LinkedIn content is a sparse canonical union",
        "images and videos lists",
        "Safe unknown fields remain open",
    )

    for statement in required_contract:
        assert statement in NORMALIZED_IDENTITY_DOCS
