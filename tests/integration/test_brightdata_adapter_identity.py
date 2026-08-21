from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountIdentityConflictError,
    AccountInput,
    AccountKind,
    AdapterInstanceOrdinal,
    AdapterOperation,
    AdapterPostRequest,
    BrightDataAdapterConfig,
    BrightDataCompanyIdentity,
    BrightDataLinkedInAdapter,
    BrightDataPersonIdentity,
    Platform,
    ResolvedAccountIdentity,
    ResolvedAdapterDrivers,
    SecretStr,
    SyntheticBrightDataClient,
    UnresolvedAccountIdentity,
    _account,
    bootstrap_runtime,
    date,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
async def test_decorated_capabilities_and_bootstrap_are_exact() -> None:
    # Given
    locators = (parse_linkedin_locator("https://linkedin.com/in/person"),)
    account_input = AccountInput(
        locators=locators,
        bright_data_api_keys=(
            SecretStr("synthetic-a"),
            SecretStr("synthetic-a"),
            SecretStr("synthetic-b"),
        ),
    )
    clients: list[SyntheticBrightDataClient] = []

    def client_builder(_credential: str) -> SyntheticBrightDataClient:
        client = SyntheticBrightDataClient()
        clients.append(client)
        return client

    # When
    runtime = bootstrap_runtime(
        account_input,
        BrightDataAdapterConfig(_NOW),
        client_builder=client_builder,
    )

    # Then
    assert len(clients) == 2
    assert runtime.registry.driver_classes == (BrightDataLinkedInAdapter,)
    metadata = BrightDataLinkedInAdapter.adapter_metadata
    assert metadata.operations == (
        AdapterOperation.RESOLVE_ACCOUNT_IDENTITY,
        AdapterOperation.COLLECT_ACCOUNT_POSTS,
        AdapterOperation.DISCOVER_LOCATOR_POSTS,
    )
    assert metadata.account_kinds == (AccountKind.PERSON, AccountKind.COMPANY)
    for operation in metadata.operations:
        for kind in metadata.account_kinds:
            resolution = runtime.registry.resolve(
                platform=Platform.LINKEDIN,
                operation=operation,
                account_kind=kind,
            )
            assert isinstance(resolution, ResolvedAdapterDrivers)
            assert resolution.driver_classes == (BrightDataLinkedInAdapter,)
    assert not hasattr(BrightDataLinkedInAdapter, "collect_post_by_url")


@pytest.mark.anyio
async def test_person_and_company_identity_use_only_stable_numeric_ids() -> None:
    # Given
    person_url = "https://www.linkedin.com/in/person/"
    company_url = "https://www.linkedin.com/company/company/"
    client = SyntheticBrightDataClient(
        person_identities=(
            BrightDataPersonIdentity(linkedin_num_id="101", url=person_url),
        ),
        company_identities=(
            BrightDataCompanyIdentity(company_id="202", url=company_url),
        ),
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )

    # When
    outcomes = await adapter.resolve_account_identity(
        (
            parse_linkedin_locator(person_url),
            parse_linkedin_locator(company_url),
        ),
        (),
    )

    # Then
    assert [
        outcome.account.id
        for outcome in outcomes
        if isinstance(outcome, ResolvedAccountIdentity)
    ] == [
        "linkedin:person:101",
        "linkedin:company:202",
    ]


@pytest.mark.anyio
async def test_unknown_non_numeric_identity_is_unresolved_and_unpersisted() -> None:
    # Given
    client = SyntheticBrightDataClient(
        person_identities=(BrightDataPersonIdentity(linkedin_num_id="slug-fallback"),)
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )

    # When
    outcomes = await adapter.resolve_account_identity(
        (parse_linkedin_locator("https://linkedin.com/in/unknown"),), ()
    )

    # Then
    assert outcomes == (UnresolvedAccountIdentity(),)


@pytest.mark.anyio
async def test_adapter_migrates_slug_alias_without_changing_stable_identity() -> None:
    # Given
    existing = _account(AccountKind.PERSON, "101", "old-slug")
    new_url = "https://www.linkedin.com/in/new-slug/"
    client = SyntheticBrightDataClient(
        person_identities=(
            BrightDataPersonIdentity(linkedin_num_id="101", url=new_url),
        )
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )

    # When
    outcomes = await adapter.resolve_account_identity(
        (parse_linkedin_locator(new_url),),
        (existing,),
    )

    # Then
    resolved = outcomes[0]
    assert isinstance(resolved, ResolvedAccountIdentity)
    assert resolved.account.id == existing.id
    assert resolved.account.url_aliases == (
        new_url,
        existing.profile_url,
    )


@pytest.mark.anyio
async def test_adapter_rejects_known_alias_returned_with_another_stable_id() -> None:
    # Given
    existing = _account(AccountKind.PERSON, "101", "known")
    client = SyntheticBrightDataClient(
        person_identities=(
            BrightDataPersonIdentity(
                linkedin_num_id="202",
                url=existing.profile_url,
            ),
        )
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )

    # When / Then
    with pytest.raises(AccountIdentityConflictError):
        _ = await adapter.resolve_account_identity(
            (parse_linkedin_locator("https://linkedin.com/in/new-slug"),),
            (existing,),
        )


@pytest.mark.anyio
async def test_known_alias_skips_identity_call_and_can_collect_zero_posts() -> None:
    # Given
    account = _account(AccountKind.PERSON, "101", "known")
    client = SyntheticBrightDataClient(
        person_identities=(
            BrightDataPersonIdentity(linkedin_num_id="101", url=account.profile_url),
        )
    )
    adapter = BrightDataLinkedInAdapter(
        client, AdapterInstanceOrdinal(0), BrightDataAdapterConfig(_NOW)
    )
    request = AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20))

    # When
    identity = await adapter.resolve_account_identity(
        (parse_linkedin_locator(account.profile_url),), (account,)
    )
    result = await adapter.collect_account_posts((request,))

    # Then
    assert [
        (call.operation, call.kind, call.urls, call.windows) for call in client.calls
    ] == [
        (
            "posts",
            AccountKind.PERSON,
            (account.profile_url,),
            ((request.start_date, request.end_date, True),),
        )
    ]
    assert identity == (ResolvedAccountIdentity(account),)
    assert result.accounts[0].posts == ()
    assert result.accounts[0].source_records == ()
    assert result.accounts[0].skipped.total == 0
