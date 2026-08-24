from __future__ import annotations

__test__ = False

from tests.integration.test_brightdata_adapter_support import (
    _NOW,
    AccountKind,
    AdapterPostRequest,
    BrightDataAdapterConfig,
    RuntimeInput,
    SecretStr,
    SourceId,
    SourceInput,
    SyntheticBrightDataClient,
    _account,
    _post,
    bootstrap_runtime,
    date,
    parse_linkedin_locator,
    pytest,
)


@pytest.mark.anyio
async def test_empty_source_set_is_rejected_before_client_creation() -> None:
    account = _account(AccountKind.PERSON, "person")

    with pytest.raises(ValueError):
        _ = RuntimeInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            sources=(),
        )


@pytest.mark.anyio
async def test_public_runtime_preserves_all_post_types_through_router() -> None:
    account = _account(AccountKind.PERSON, "person")
    client = SyntheticBrightDataClient(
        person_posts=(
            _post(account, "original"),
            _post(account, "reply", "reply"),
        )
    )
    runtime = bootstrap_runtime(
        RuntimeInput(
            locators=(parse_linkedin_locator(account.profile_url),),
            sources=(
                SourceInput(
                    source_id=SourceId.BRIGHTDATA,
                    credential=SecretStr("synthetic-one"),
                ),
            ),
        ),
        BrightDataAdapterConfig(_NOW),
        client_builder=lambda _credential: client,
    )

    result = await runtime.router.route(
        (AdapterPostRequest(account, date(2026, 8, 13), date(2026, 8, 20)),)
    )

    assert len(result.posts) == 2
    assert {post.platform_post_id for post in result.posts} == {"original", "reply"}
    assert {post.type for post in result.posts} == {"post", "reply"}
