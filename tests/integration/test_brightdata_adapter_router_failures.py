from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountInput,
    AccountKind,
    BrightDataAdapterConfig,
    BrightDataError,
    BrightDataErrorCategory,
    BrightDataPersonIdentity,
    InstanceHealthStatus,
    RouterDiagnosticCategory,
    RouterRunStatus,
    SecretStr,
    SyntheticBrightDataClient,
    UnresolvedAccountIdentity,
    _account,
    bootstrap_runtime,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("category", "expected_health", "expected_diagnostic"),
    [
        (
            BrightDataErrorCategory.QUOTA,
            InstanceHealthStatus.QUOTA_EXHAUSTED,
            RouterDiagnosticCategory.QUOTA_DISABLED,
        ),
        (
            BrightDataErrorCategory.AUTH,
            InstanceHealthStatus.INVALID_CREDENTIAL,
            RouterDiagnosticCategory.CREDENTIAL_DISABLED,
        ),
        (
            BrightDataErrorCategory.RETRYABLE,
            InstanceHealthStatus.HEALTHY,
            None,
        ),
    ],
)
async def test_identity_router_classified_failover_uses_each_instance_once(
    category: BrightDataErrorCategory,
    expected_health: InstanceHealthStatus,
    expected_diagnostic: RouterDiagnosticCategory | None,
) -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/new-person")
    clients = [
        SyntheticBrightDataClient(failure=BrightDataError(category)),
        SyntheticBrightDataClient(
            person_identities=(
                BrightDataPersonIdentity(
                    linkedin_num_id="101",
                    url=locator.canonical_url,
                ),
            )
        ),
        SyntheticBrightDataClient(),
    ]
    created = iter(clients)
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(locator,),
            bright_data_api_keys=tuple(
                SecretStr(f"synthetic-{index}") for index in range(3)
            ),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: next(created),
    )

    # When
    result = await runtime.router.resolve_identities((locator,), ())

    # Then
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert result.health[0].status is expected_health
    assert [len(client.calls) for client in clients] == [1, 1, 0]
    assert [item.category for item in result.diagnostics] == (
        [] if expected_diagnostic is None else [expected_diagnostic]
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "category",
    [BrightDataErrorCategory.INPUT, BrightDataErrorCategory.NOT_FOUND],
)
async def test_identity_router_account_failure_never_rotates_credentials(
    category: BrightDataErrorCategory,
) -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/in/unresolved")
    clients = [
        SyntheticBrightDataClient(failure=BrightDataError(category)),
        SyntheticBrightDataClient(),
    ]
    created = iter(clients)
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(locator,),
            bright_data_api_keys=(SecretStr("synthetic-a"), SecretStr("synthetic-b")),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: next(created),
    )

    # When
    result = await runtime.router.resolve_identities((locator,), ())

    # Then
    assert result.outcomes == (UnresolvedAccountIdentity(),)
    assert result.aggregate.status is RouterRunStatus.PARTIAL
    assert [len(client.calls) for client in clients] == [1, 0]


@pytest.mark.anyio
async def test_identity_router_accepted_snapshot_failure_never_reroutes() -> None:
    # Given
    locator = parse_linkedin_locator("https://linkedin.com/company/unresolved")
    clients = [
        SyntheticBrightDataClient(
            failure=BrightDataError(
                BrightDataErrorCategory.SNAPSHOT_TIMEOUT,
                snapshot_accepted=True,
            )
        ),
        SyntheticBrightDataClient(),
    ]
    created = iter(clients)
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(locator,),
            bright_data_api_keys=(SecretStr("synthetic-a"), SecretStr("synthetic-b")),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: next(created),
    )

    # When
    result = await runtime.router.resolve_identities((locator,), ())

    # Then
    assert result.outcomes == (UnresolvedAccountIdentity(),)
    assert [len(client.calls) for client in clients] == [1, 0]


@pytest.mark.anyio
async def test_identity_conflict_aborts_without_candidates_or_provider_text() -> None:
    # Given
    known = _account(AccountKind.PERSON, "100", "known")
    locator = parse_linkedin_locator("https://linkedin.com/in/new-person")
    client = SyntheticBrightDataClient(
        person_identities=(
            BrightDataPersonIdentity.model_validate(
                {
                    "linkedin_num_id": "999",
                    "url": known.profile_url,
                    "instruction": "provider prompt canary",
                }
            ),
        )
    )
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(locator,),
            bright_data_api_keys=(SecretStr("credential-canary"),),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )

    # When
    result = await runtime.router.resolve_identities((locator,), (known,))

    # Then
    assert result.aggregate.status is RouterRunStatus.ABORTED
    assert result.outcomes == ()
    assert result.diagnostics[0].category is RouterDiagnosticCategory.SCHEMA_ABORT
    assert "canary" not in repr(result)
    assert "prompt" not in repr(result)
