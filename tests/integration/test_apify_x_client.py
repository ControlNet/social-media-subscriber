from __future__ import annotations

import json
from datetime import date

import httpx2
import pytest
from structlog.testing import capture_logs

from social_media_subscriber.providers.apify.constants import (
    APIFY_X_ACTOR,
)
from social_media_subscriber.providers.apify.errors import (
    ApifyError,
    ApifyErrorCategory,
)
from social_media_subscriber.providers.apify.x_client import ApifyXClient
from social_media_subscriber.providers.apify.x_requests import ApifyXPostInput
from social_media_subscriber.providers.http import HttpClientConfig

_POST = {
    "author": {"id": "3001", "username": "synthetic_ada"},
    "bookmarkCount": 1,
    "createdAt": "Wed Aug 19 09:00:00 +0000 2026",
    "id": "2001",
    "isQuoteStatus": False,
    "isReply": False,
    "likeCount": 7,
    "quoteCount": 2,
    "replyCount": 3,
    "retweetCount": 5,
    "text": "Synthetic X post",
    "type": "tweet",
    "url": "https://x.com/synthetic_ada/status/2001",
    "viewCount": 101,
}


def _run() -> dict[str, object]:
    return {
        "data": {
            "defaultDatasetId": "synthetic-dataset",
            "defaultKeyValueStoreId": "synthetic-store",
            "id": "synthetic-run",
            "status": "SUCCEEDED",
        }
    }


def _report(
    *,
    outcome: str = "partial",
    completion_reason: str = "source_exhausted",
    real_rows: int = 1,
    diagnostic_rows: int = 0,
    failed_subtargets: int = 0,
) -> dict[str, object]:
    return {
        "actor": "xquik/x-tweet-scraper",
        "anomalyCounts": {},
        "outcome": outcome,
        "results": {
            "completionReason": completion_reason,
            "diagnosticRows": diagnostic_rows,
            "estimatedChargeUsd": 0.00015,
            "failedSubtargets": failed_subtargets,
            "realRows": real_rows,
            "totalDuplicates": 0,
            "totalPushed": real_rows + diagnostic_rows,
        },
        "schemaVersion": 1,
        "userPricingTier": "FREE",
        "version": "synthetic",
    }


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["complete", "partial"])
async def test_x_client_starts_unpriced_search_run_and_accepts_exhausted_dataset(
    outcome: str,
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(200, json=_report(outcome=outcome))
        if request.url.path.endswith("/items"):
            return httpx2.Response(200, json=[_POST])
        raise AssertionError(request.url.path)

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyXPostInput(
            "https://x.com/synthetic_ada/",
            date(2026, 8, 17),
            date(2026, 8, 20),
            is_initial_collection=False,
        )
    )

    assert tuple(post.id for post in posts) == ("2001",)
    start = requests[0]
    assert start.headers["authorization"] == "Bearer synthetic-token"
    assert start.url.params["waitForFinish"] == "0"
    assert "maxTotalChargeUsd" not in start.url.params
    assert json.loads(start.content) == {
        "fieldStyle": "camelCase",
        "mode": "search",
        "outputPreset": "nested",
        "outputVariant": "rich",
        "queryType": "Latest",
        "searchTerms": ["from:synthetic_ada since:2026-08-17 until:2026-08-21"],
    }
    assert requests[1].url.path.endswith("/records/run-report")
    assert requests[2].url.params["clean"] == "true"
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_maps_strict_zero_output_diagnostic_to_empty_posts() -> None:
    diagnostic = {
        "actor": "xquik/x-tweet-scraper",
        "id": "diag:zero-output:synthetic",
        "message": "Synthetic zero output",
        "resultType": "diagnostic",
        "status": "zero-output",
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(
                200,
                json=_report(
                    outcome="zero-output",
                    completion_reason="zero_output",
                    real_rows=0,
                    diagnostic_rows=1,
                ),
            )
        return httpx2.Response(200, json=[diagnostic])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyXPostInput(
            "https://x.com/synthetic_ada/",
            date(2013, 10, 13),
            date(2019, 2, 26),
        )
    )

    assert posts == ()
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("completion_reason", ["budget_limited", "upstream_limit"])
async def test_x_client_accepts_partial_run_with_valid_posts(
    completion_reason: str,
) -> None:
    dataset_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal dataset_requests
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(
                200,
                json=_report(
                    completion_reason=completion_reason,
                ),
            )
        dataset_requests += 1
        return httpx2.Response(200, json=[_POST])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with capture_logs() as logs:
        posts = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2006, 3, 21),
                date(2026, 8, 20),
            )
        )

    assert tuple(post.id for post in posts) == ("2001",)
    assert dataset_requests == 1
    assert any(log["event"] == "provider.x.partial_results" for log in logs)
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_rejects_untyped_or_mock_dataset_rows() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(200, json=_report())
        return httpx2.Response(200, json=[{"id": "2001", "type": "mock_tweet"}])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.SCHEMA
    assert captured.value.run_accepted is True
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("completion_reason", ["source_exhausted", "budget_limited"])
async def test_x_client_rejects_report_dataset_count_mismatch(
    completion_reason: str,
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(
                200, json=_report(real_rows=2, completion_reason=completion_reason)
            )
        return httpx2.Response(200, json=[_POST])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.SCHEMA
    assert captured.value.run_accepted is True
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_accepts_report_anomalies_with_safe_warning() -> None:
    dataset_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal dataset_requests
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            report = _report()
            report["anomalyCounts"] = {
                "non-retryable-http-error": 1,
                "target-not-found": 1,
                "synthetic-private-anomaly": 0,
            }
            return httpx2.Response(200, json=report)
        dataset_requests += 1
        return httpx2.Response(200, json=[_POST])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with capture_logs() as logs:
        posts = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert tuple(post.id for post in posts) == ("2001",)
    assert dataset_requests == 1
    warning = next(log for log in logs if log["event"] == "provider.x.partial_results")
    assert warning["log_level"] == "warning"
    assert warning["posts"] == 1
    assert warning["anomaly_count"] == 2
    assert "synthetic-private-anomaly" not in json.dumps(warning)
    assert "synthetic-token" not in json.dumps(warning)
    assert "Synthetic X post" not in json.dumps(warning)
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("completion_reason", "failed_subtargets", "anomaly_count"),
    [
        ("source_exhausted", 0, 0),
        ("budget_limited", 0, 0),
        ("source_exhausted", 1, 0),
        ("source_exhausted", 0, 1),
        ("pagination_safety_limit", 0, 1),
    ],
)
async def test_x_client_rejects_incomplete_empty_dataset(
    completion_reason: str, failed_subtargets: int, anomaly_count: int
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            report = _report(
                real_rows=0,
                completion_reason=completion_reason,
                failed_subtargets=failed_subtargets,
            )
            report["anomalyCounts"] = {"syntheticAnomaly": anomaly_count}
            return httpx2.Response(200, json=report)
        return httpx2.Response(200, json=[])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.INCOMPLETE
    assert captured.value.run_accepted is True
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_accepts_valid_posts_despite_failed_subtargets() -> None:
    dataset_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal dataset_requests
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(200, json=_report(failed_subtargets=1))
        dataset_requests += 1
        return httpx2.Response(200, json=[_POST])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with capture_logs() as logs:
        posts = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert tuple(post.id for post in posts) == ("2001",)
    assert dataset_requests == 1
    warning = next(log for log in logs if log["event"] == "provider.x.partial_results")
    assert warning["failed_subtargets"] == 1
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_rejects_missing_completion_report_as_accepted_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        return httpx2.Response(404)

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.SCHEMA
    assert captured.value.status == 404
    assert captured.value.run_accepted is True
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_accepts_pagination_safety_limit_dataset() -> None:
    dataset_requests = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal dataset_requests
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(
                200,
                json=_report(completion_reason="pagination_safety_limit"),
            )
        dataset_requests += 1
        return httpx2.Response(200, json=[_POST])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyXPostInput(
            "https://x.com/synthetic_ada/",
            date(2026, 8, 17),
            date(2026, 8, 20),
        )
    )

    assert tuple(post.id for post in posts) == ("2001",)
    assert dataset_requests == 1
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_accepts_empty_pagination_safety_limit_dataset() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(
                200,
                json=_report(
                    completion_reason="pagination_safety_limit",
                    real_rows=0,
                ),
            )
        return httpx2.Response(200, json=[])

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyXPostInput(
            "https://x.com/synthetic_ada/",
            date(2026, 8, 17),
            date(2026, 8, 20),
        )
    )

    assert posts == ()
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_enforces_complete_inclusive_local_date_window() -> None:
    timestamps = (
        "Sun Aug 16 09:00:00 +0000 2026",
        "Mon Aug 17 09:00:00 +0000 2026",
        "Thu Aug 20 09:00:00 +0000 2026",
        "Fri Aug 21 09:00:00 +0000 2026",
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == f"/v2/acts/{APIFY_X_ACTOR}/runs":
            return httpx2.Response(201, json=_run())
        if request.url.path.endswith("/records/run-report"):
            return httpx2.Response(200, json=_report(real_rows=len(timestamps)))
        return httpx2.Response(
            200,
            json=[
                _POST
                | {
                    "createdAt": timestamp,
                    "id": str(2000 + index),
                    "url": f"https://x.com/synthetic_ada/status/{2000 + index}",
                }
                for index, timestamp in enumerate(timestamps)
            ],
        )

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    posts = await client.collect_posts(
        ApifyXPostInput(
            "https://x.com/synthetic_ada/",
            date(2026, 8, 17),
            date(2026, 8, 20),
        )
    )

    assert tuple(post.id for post in posts) == ("2001", "2002")
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_explicit_start_rejection_remains_eligible_for_failover() -> (
    None
):
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429)

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.RETRYABLE
    assert captured.value.status == 429
    assert captured.value.run_accepted is False
    await client.aclose()


@pytest.mark.anyio
async def test_x_client_ambiguous_start_timeout_prevents_duplicate_paid_run() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        message = "synthetic timeout"
        raise httpx2.ReadTimeout(message, request=request)

    client = ApifyXClient(
        "synthetic-token",
        HttpClientConfig(base_url="https://api.apify.test"),
        transport=httpx2.MockTransport(handler),
    )

    with pytest.raises(ApifyError) as captured:
        _ = await client.collect_posts(
            ApifyXPostInput(
                "https://x.com/synthetic_ada/",
                date(2026, 8, 17),
                date(2026, 8, 20),
            )
        )

    assert captured.value.category is ApifyErrorCategory.TIMEOUT
    assert captured.value.run_accepted is True
    await client.aclose()
