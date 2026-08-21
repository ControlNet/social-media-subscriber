from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountInput,
    AccountKind,
    AccountRouteFailed,
    AccountRouteFailureCategory,
    AdapterOperation,
    AdapterPostRequest,
    BrightDataAdapterConfig,
    BrightDataCompanyIdentity,
    BrightDataError,
    BrightDataErrorCategory,
    BrightDataPersonIdentity,
    ResolvedAccountIdentity,
    RouterRunStatus,
    SecretStr,
    SyntheticBrightDataClient,
    _account,
    _post,
    bootstrap_runtime,
    date,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
async def test_empty_bootstrap_pool_reports_exhaustion_without_client_creation() -> (
    None
):
    # Given
    account = _account(AccountKind.PERSON, "101", "person")
    account_input = AccountInput(
        locators=(parse_linkedin_locator(account.profile_url),),
        bright_data_api_keys=(),
    )
    clients: list[SyntheticBrightDataClient] = []

    def client_builder(_credential: str) -> SyntheticBrightDataClient:
        client = SyntheticBrightDataClient()
        clients.append(client)
        return client

    runtime = bootstrap_runtime(
        account_input,
        BrightDataAdapterConfig(_NOW),
        client_builder=client_builder,
    )

    # When
    result = await runtime.router.route(
        (AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20)),),
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )

    # Then
    assert result.accounts == (
        AccountRouteFailed(account.id, AccountRouteFailureCategory.POOL_EXHAUSTED),
    )
    assert clients == []


@pytest.mark.anyio
async def test_public_runtime_routes_identity_through_existing_instance_pool() -> None:
    # Given
    known = _account(AccountKind.PERSON, "100", "known")
    locators = (
        parse_linkedin_locator(known.profile_url),
        parse_linkedin_locator("https://linkedin.com/in/new-person"),
        parse_linkedin_locator("https://linkedin.com/company/new-company"),
    )
    client = SyntheticBrightDataClient(
        person_identities=(
            BrightDataPersonIdentity(
                linkedin_num_id="101",
                url=locators[1].canonical_url,
            ),
        ),
        company_identities=(
            BrightDataCompanyIdentity(
                company_id="202",
                url=locators[2].canonical_url,
            ),
        ),
    )
    runtime = bootstrap_runtime(
        AccountInput(
            locators=locators,
            bright_data_api_keys=(SecretStr("synthetic-one"),),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )

    # When
    result = await runtime.router.resolve_identities(locators, (known,))

    # Then
    assert result.aggregate.resolved_locators == 3
    assert result.aggregate.unresolved_locators == 0
    resolved_ids = [
        outcome.account.id
        for outcome in result.outcomes
        if isinstance(outcome, ResolvedAccountIdentity)
    ]
    assert resolved_ids == [
        known.id,
        "linkedin:person:101",
        "linkedin:company:202",
    ]
    assert [call.kind for call in client.calls] == [
        AccountKind.PERSON,
        AccountKind.COMPANY,
    ]


@pytest.mark.anyio
async def test_public_runtime_preserves_source_records_and_skips_through_router() -> (
    None
):
    # Given
    account = _account(AccountKind.PERSON, "101", "person")
    client = SyntheticBrightDataClient(
        person_posts=(
            _post(account, "original"),
            _post(account, "reply", "reply"),
        )
    )
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            bright_data_api_keys=(SecretStr("synthetic-one"),),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )

    # When
    result = await runtime.router.route(
        (AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20)),),
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
    )

    # Then
    assert len(result.posts) == 1
    assert [record.platform_post_id for record in result.source_records] == [
        "original",
        "reply",
    ]
    assert result.skipped.replies == 1


@pytest.mark.anyio
async def test_identity_router_known_alias_requires_zero_provider_io() -> None:
    # Given
    known = _account(AccountKind.PERSON, "100", "known")
    locator = parse_linkedin_locator(known.profile_url)
    client = SyntheticBrightDataClient(
        failure=BrightDataError(BrightDataErrorCategory.SCHEMA)
    )
    runtime = bootstrap_runtime(
        AccountInput(
            locators=(locator,),
            bright_data_api_keys=(SecretStr("synthetic-one"),),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )

    # When
    result = await runtime.router.resolve_identities((locator,), (known,))

    # Then
    assert result.aggregate.status is RouterRunStatus.SUCCESS
    assert result.outcomes == (ResolvedAccountIdentity(known),)
    assert client.calls == []
