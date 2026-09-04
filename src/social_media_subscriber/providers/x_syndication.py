"""Best-effort referenced X post enrichment through syndication data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final, Literal, Protocol, Self, final
from urllib.parse import urlsplit

import anyio
import httpx2
import structlog
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from social_media_subscriber.domain.platform import Platform

if TYPE_CHECKING:
    from types import TracebackType

    from social_media_subscriber.domain.post import Post

_BASE_URL: Final = "https://cdn.syndication.twimg.com"
_REPOST: Final = re.compile(r"^RT @([A-Za-z0-9_]{1,15}): ")
_STATUS_ID: Final = re.compile(r"^[0-9]+$")
_LOGGER = structlog.stdlib.get_logger()
_TIMEOUT: Final = httpx2.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
_MAX_CONCURRENCY: Final = 4
_HTTP_OK: Final = 200
_HTTP_FORBIDDEN: Final = 403
_HTTP_NOT_FOUND: Final = 404
_HTTP_RATE_LIMIT: Final = 429
_HTTP_SERVER_ERROR: Final = 500


class _SyndicationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        validate_default=True,
    )


class SyndicationUser(_SyndicationModel):
    """Referenced post author fields consumed by the public snapshot."""

    name: str = Field(min_length=1)
    screen_name: str = Field(min_length=1)
    profile_image_url_https: str = Field(min_length=1)


class SyndicationTweet(_SyndicationModel):
    """Minimum trusted tweet-result response with open media entries."""

    id_str: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    text: str
    user: SyndicationUser
    media_details: list[JsonValue] = Field(default_factory=list, alias="mediaDetails")


class SyndicationMissCategory(StrEnum):
    """Safe aggregate failure categories for the unofficial endpoint."""

    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    HTTP = "http"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SCHEMA = "schema"
    INTEGRITY = "integrity"


@dataclass(frozen=True, slots=True)
class SyndicationFetchResult:
    """One sanitized fetch outcome without response data on failure."""

    tweet: SyndicationTweet | None
    miss_category: SyndicationMissCategory | None


class SyndicationClientContract(Protocol):
    """Unauthenticated transport used by the shared enrichment service."""

    async def fetch(self, status_id: str) -> SyndicationFetchResult:
        """Fetch one referenced status or return a sanitized miss."""
        ...

    async def aclose(self) -> None:
        """Close the owned transport."""
        ...


@dataclass(frozen=True, slots=True)
class SyndicationClientConfig:
    """Fixed endpoint configuration supporting contained transport tests."""

    base_url: str = _BASE_URL


_DEFAULT_CONFIG: Final = SyndicationClientConfig()


@final
class XMediaSyndicationClient:
    """Short-timeout, credential-free client for tweet-result responses."""

    def __init__(
        self,
        config: SyndicationClientConfig = _DEFAULT_CONFIG,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Create an independent transport with no authorization header."""
        selected_transport = transport or httpx2.AsyncHTTPTransport(
            http2=True, retries=0
        )
        self._http = httpx2.AsyncClient(
            base_url=config.base_url,
            headers={"Accept": "application/json"},
            transport=selected_transport,
            timeout=_TIMEOUT,
            follow_redirects=False,
        )

    async def __aenter__(self) -> Self:
        """Return the opened client."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the owned client on context exit."""
        _ = exc_type, exc_value, traceback
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned connection pool."""
        await self._http.aclose()

    async def fetch(self, status_id: str) -> SyndicationFetchResult:
        """Fetch one status through the fixed public endpoint."""
        try:
            response = await self._http.get(
                "/tweet-result",
                params={"id": status_id, "lang": "en", "token": "1"},
            )
        except httpx2.TimeoutException:
            return _miss(SyndicationMissCategory.TIMEOUT)
        except httpx2.RequestError:
            return _miss(SyndicationMissCategory.NETWORK)
        if response.status_code != _HTTP_OK:
            return _miss(_status_category(response.status_code))
        try:
            return SyndicationFetchResult(
                SyndicationTweet.model_validate_json(response.content), None
            )
        except ValidationError:
            return _miss(SyndicationMissCategory.SCHEMA)


def _miss(category: SyndicationMissCategory) -> SyndicationFetchResult:
    return SyndicationFetchResult(None, category)


def _status_category(status: int) -> SyndicationMissCategory:
    if status == _HTTP_FORBIDDEN:
        return SyndicationMissCategory.FORBIDDEN
    if status == _HTTP_NOT_FOUND:
        return SyndicationMissCategory.NOT_FOUND
    if status == _HTTP_RATE_LIMIT:
        return SyndicationMissCategory.RATE_LIMIT
    if status >= _HTTP_SERVER_ERROR:
        return SyndicationMissCategory.SERVER
    return SyndicationMissCategory.HTTP


@dataclass(frozen=True, slots=True)
class XMediaEnrichmentReport:
    """Safe aggregate result for one enrichment batch."""

    scanned_posts: int
    eligible_posts: int
    enriched_posts: int
    missed_posts: int
    media_items: int


@dataclass(frozen=True, slots=True)
class XMediaEnrichmentResult:
    """Enriched immutable posts and their safe aggregate report."""

    posts: tuple[Post, ...]
    report: XMediaEnrichmentReport


class XMediaEnricherContract(Protocol):
    """Shared post-to-post enrichment capability."""

    async def enrich(self, posts: tuple[Post, ...]) -> XMediaEnrichmentResult:
        """Best-effort enrich eligible X reposts and replies."""
        ...

    async def aclose(self) -> None:
        """Close the owned syndication transport."""
        ...


@dataclass(frozen=True, slots=True)
class _Reference:
    requested_id: str | None
    expected_id: str | None
    expected_username: str | None


@final
class XMediaEnricher:
    """Enrich immutable X posts while preserving every failed record."""

    def __init__(
        self,
        client: SyndicationClientContract,
        *,
        max_concurrency: int = _MAX_CONCURRENCY,
    ) -> None:
        """Bind the shared client and the per-batch concurrency limit."""
        self._client = client
        self._max_concurrency = max_concurrency

    async def aclose(self) -> None:
        """Close the owned syndication transport."""
        await self._client.aclose()

    async def enrich(self, posts: tuple[Post, ...]) -> XMediaEnrichmentResult:
        """Fetch deduplicated references and return stable enriched posts."""
        references = tuple(_reference(post) for post in posts)
        requested_ids = tuple(
            dict.fromkeys(
                reference.requested_id
                for reference in references
                if reference is not None and reference.requested_id is not None
            )
        )
        fetched = await self._fetch_all(requested_ids)
        enriched: list[Post] = []
        eligible = 0
        successful = 0
        media_items = 0
        miss_counts: dict[str, int] = {}
        for post, reference in zip(posts, references, strict=True):
            if reference is None:
                enriched.append(post)
                continue
            eligible += 1
            if reference.requested_id is None:
                _increment(miss_counts, SyndicationMissCategory.INTEGRITY)
                enriched.append(post)
                continue
            fetch = fetched[reference.requested_id]
            if fetch.tweet is None:
                category = fetch.miss_category or SyndicationMissCategory.SCHEMA
                _increment(miss_counts, category)
                enriched.append(post)
                continue
            quoted = _project(fetch.tweet, reference)
            if quoted is None:
                _increment(miss_counts, SyndicationMissCategory.INTEGRITY)
                enriched.append(post)
                continue
            content = dict(post.content)
            content["quotedTweet"] = quoted
            enriched.append(post.model_copy(update={"content": content}))
            successful += 1
            media = quoted.get("media")
            media_items += len(media) if isinstance(media, list) else 0
        report = XMediaEnrichmentReport(
            len(posts), eligible, successful, eligible - successful, media_items
        )
        await _LOGGER.ainfo(
            "x.media_enrichment.complete",
            scanned_posts=report.scanned_posts,
            eligible_posts=report.eligible_posts,
            enriched_posts=report.enriched_posts,
            missed_posts=report.missed_posts,
            media_items=report.media_items,
            miss_categories=miss_counts,
        )
        return XMediaEnrichmentResult(tuple(enriched), report)

    async def _fetch_all(
        self, requested_ids: tuple[str, ...]
    ) -> dict[str, SyndicationFetchResult]:
        results: dict[str, SyndicationFetchResult] = {}
        limiter = anyio.CapacityLimiter(self._max_concurrency)

        async def fetch_one(status_id: str) -> None:
            async with limiter:
                results[status_id] = await self._client.fetch(status_id)

        async with anyio.create_task_group() as group:
            for status_id in requested_ids:
                _ = group.start_soon(fetch_one, status_id)
        return results


def _increment(counts: dict[str, int], category: SyndicationMissCategory) -> None:
    counts[category.value] = counts.get(category.value, 0) + 1


def _reference(post: Post) -> _Reference | None:
    if post.platform is not Platform.X or "quotedTweet" in post.content:
        return None
    if post.type == "reply":
        value = post.content.get("inReplyToId")
        requested = (
            value if isinstance(value, str) and _STATUS_ID.fullmatch(value) else None
        )
        return _Reference(requested, requested, None)
    if post.type != "post":
        return None
    text = post.content.get("text")
    match = _REPOST.match(text) if isinstance(text, str) else None
    if match is None:
        return None
    return _Reference(str(post.platform_post_id), None, match.group(1))


def _project(
    tweet: SyndicationTweet, reference: _Reference
) -> dict[str, JsonValue] | None:
    if not _STATUS_ID.fullmatch(tweet.id_str):
        return None
    if reference.expected_id is not None and tweet.id_str != reference.expected_id:
        return None
    if (
        reference.expected_username is not None
        and tweet.user.screen_name.casefold() != reference.expected_username.casefold()
    ):
        return None
    if not _valid_username(tweet.user.screen_name):
        return None
    profile = tweet.user.profile_image_url_https
    if not _https_url(profile):
        return None
    media: list[JsonValue] = []
    for item in tweet.media_details:
        projected_media = _project_media(item)
        if projected_media is not None:
            media.append(projected_media)
    projected: dict[str, JsonValue] = {
        "id": tweet.id_str,
        "url": f"https://x.com/{tweet.user.screen_name}/status/{tweet.id_str}",
        "createdAt": tweet.created_at,
        "text": tweet.text,
        "author": {
            "name": tweet.user.name,
            "username": tweet.user.screen_name,
            "profilePicture": profile,
        },
        "media": media,
    }
    return projected


def _project_media(value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    media_type = value.get("type")
    if media_type not in {"photo", "video", "animated_gif"}:
        return None
    media_url = value.get("media_url_https")
    original = value.get("original_info")
    if (
        not isinstance(media_url, str)
        or not _https_host(media_url, "pbs.twimg.com")
        or not isinstance(original, dict)
    ):
        return None
    width = original.get("width")
    height = original.get("height")
    if not _positive_int(width) or not _positive_int(height):
        return None
    projected: dict[str, JsonValue] = {
        "type": media_type,
        "mediaUrl": media_url,
        "width": width,
        "height": height,
    }
    marker = value.get("url")
    if isinstance(marker, str) and _https_host(marker, "t.co"):
        projected["url"] = marker
    alt_text = value.get("ext_alt_text")
    if isinstance(alt_text, str) and alt_text.strip():
        projected["altText"] = alt_text
    if media_type in {"video", "animated_gif"}:
        variants = _project_variants(value.get("video_info"))
        if variants:
            projected["videoVariants"] = variants
    return projected


def _project_variants(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, dict):
        return []
    variants = value.get("variants")
    if not isinstance(variants, list):
        return []
    projected: list[JsonValue] = []
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        content_type = variant.get("content_type")
        url = variant.get("url")
        if (
            content_type != "video/mp4"
            or not isinstance(url, str)
            or not _https_host(url, "video.twimg.com")
        ):
            continue
        item: dict[str, JsonValue] = {"contentType": content_type, "url": url}
        bitrate = variant.get("bitrate")
        if _positive_int(bitrate):
            item["bitrate"] = bitrate
        projected.append(item)
    return sorted(
        projected,
        key=_variant_sort_key,
    )


def _variant_sort_key(item: JsonValue) -> tuple[int, str]:
    if not isinstance(item, dict):
        return (-1, "")
    bitrate = item.get("bitrate")
    url = item.get("url")
    return (
        bitrate if isinstance(bitrate, int) and not isinstance(bitrate, bool) else -1,
        url if isinstance(url, str) else "",
    )


def _valid_username(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_]{1,15}", value))


def _positive_int(value: JsonValue) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and parsed.hostname is not None


def _https_host(
    value: str, host: Literal["pbs.twimg.com", "t.co", "video.twimg.com"]
) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and parsed.hostname == host
