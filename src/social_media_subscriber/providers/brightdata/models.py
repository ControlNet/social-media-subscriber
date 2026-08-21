"""Frozen models for validated Bright Data LinkedIn responses."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, ClassVar, Final, Self, assert_never
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.time import canonical_utc
from social_media_subscriber.providers.brightdata.normalization_errors import (
    BrightDataNormalizationError,
    BrightDataNormalizationErrorCategory,
)

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)
type BrightDataSnapshotId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]

_POST_TIMESTAMP_ERROR: Final = "provider_post_timestamp"
_POST_TIMESTAMP_MESSAGE: Final = "provider post timestamp must be timezone-aware UTC"
_POST_ACTOR_ERROR: Final = "provider_post_actor"
_POST_ACTOR_MESSAGE: Final = "provider post must contain an actor URL"
_JSON_OBJECT_ADAPTER: Final[TypeAdapter[dict[str, JsonValue]]] = TypeAdapter(
    dict[str, JsonValue],
    config=ConfigDict(strict=True),
)
_HASHTAGS_ADAPTER: Final[TypeAdapter[tuple[str, ...]]] = TypeAdapter(tuple[str, ...])
_FORBIDDEN_POST_FIELDS: Final = frozenset(
    {
        "authorization",
        "error",
        "errors",
        "headers",
        "http_headers",
        "raw_body",
        "raw_response",
        "request",
        "request_id",
        "requests",
    }
)
_FORBIDDEN_FIELD_ERROR: Final = "provider_post_forbidden_field"
_FORBIDDEN_FIELD_MESSAGE: Final = "provider post contains non-persistable metadata"
_LINKEDIN_HOST: Final = re.compile(
    r"(?:linkedin\.com|www\.linkedin\.com|[a-z]{2,3}\.linkedin\.com)\Z",
    re.ASCII,
)
_UNSAFE_URL: Final = re.compile(
    r"(?:[\x00-\x1f\x7f\\]|%(?:[01][0-9a-f]|7f|2f|5c|2e))", re.IGNORECASE
)
_SENSITIVE_QUERY_KEYS: Final = frozenset(
    {"access_token", "api_key", "auth", "key", "password", "signature", "token"}
)
_TRACKING_QUERY_KEYS: Final = frozenset(
    {"lipi", "midtoken", "ref", "trk", "trackingid"}
)


class _BrightDataModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="allow",
        frozen=True,
        hide_input_in_errors=True,
        strict=True,
        validate_default=True,
    )

    @model_validator(mode="before")
    @classmethod
    def validate_recursive_json(cls, value: JsonValue) -> dict[str, JsonValue]:
        """Reject non-JSON values before open-ended provider fields are accepted."""
        return _JSON_OBJECT_ADAPTER.validate_python(value)


class BrightDataPost(_BrightDataModel):
    """Open-ended successful post response with strict canonical prerequisites."""

    id: str = Field(min_length=1)
    date_posted: str
    post_type: str = Field(min_length=1)
    url: str
    user_id: str | None = None
    use_url: str | None = None
    user_url: str | None = None
    profile_url: str | None = None
    company_url: str | None = None
    post_text: str | None = None
    hashtags: tuple[str, ...] | None = None
    embedded_links: JsonValue = None

    @model_validator(mode="before")
    @classmethod
    def reject_non_persistable_metadata(cls, value: JsonValue) -> dict[str, JsonValue]:
        """Exclude transport, request, and error material from successful posts."""
        payload = _JSON_OBJECT_ADAPTER.validate_python(value)
        normalized_keys = {key.casefold().replace("-", "_") for key in payload}
        if normalized_keys & _FORBIDDEN_POST_FIELDS:
            raise PydanticCustomError(
                _FORBIDDEN_FIELD_ERROR,
                _FORBIDDEN_FIELD_MESSAGE,
            )
        return payload

    @field_validator("hashtags", mode="before")
    @classmethod
    def parse_hashtags(cls, value: JsonValue) -> tuple[str, ...] | None:
        """Parse JSON arrays into the frozen in-memory collection type."""
        if value is None:
            return None
        return _HASHTAGS_ADAPTER.validate_python(value)

    @field_validator("date_posted")
    @classmethod
    def validate_date_posted(cls, value: str) -> str:
        """Validate the timestamp while retaining the exact provider string."""
        try:
            parsed = datetime.fromisoformat(value)
            _ = canonical_utc(parsed)
        except (ValueError, PydanticCustomError):
            raise PydanticCustomError(
                _POST_TIMESTAMP_ERROR,
                _POST_TIMESTAMP_MESSAGE,
            ) from None
        return value

    @model_validator(mode="after")
    def validate_actor_reference(self) -> Self:
        """Require at least one actor URL for later strict ownership validation."""
        actor_urls = (self.use_url, self.user_url, self.profile_url, self.company_url)
        if not any(actor_urls):
            raise PydanticCustomError(_POST_ACTOR_ERROR, _POST_ACTOR_MESSAGE)
        return self

    @property
    def payload(self) -> dict[str, JsonValue]:
        """Return the complete successful object with exact provider field names."""
        return _JSON_OBJECT_ADAPTER.validate_json(
            self.model_dump_json(exclude_unset=True)
        )


class BrightDataSnapshotEnvelope(_BrightDataModel):
    """Accepted asynchronous scrape response."""

    snapshot_id: BrightDataSnapshotId


class BrightDataSnapshotProgress(_BrightDataModel):
    """Asynchronous snapshot progress response parsed by the transport layer."""

    snapshot_id: BrightDataSnapshotId | None = None
    status: str = Field(min_length=1)


class BrightDataIncludeErrorRecord(_BrightDataModel):
    """Typed include-errors record kept outside successful source persistence."""

    error: JsonValue
    input: JsonValue = None


def canonical_post_url(value: str) -> str:
    """Return a query-free canonical LinkedIn Post URL or a safe typed failure."""
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        hostname = None
        port = -1
        parsed = urlsplit("")
    if (
        _UNSAFE_URL.search(value) is not None
        or parsed.scheme.casefold() != "https"
        or hostname is None
        or _LINKEDIN_HOST.fullmatch(hostname.casefold()) is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not parsed.path.startswith(("/posts/", "/feed/update/"))
    ):
        raise BrightDataNormalizationError(
            BrightDataNormalizationErrorCategory.POST_URL
        )
    return urlunsplit(("https", "www.linkedin.com", parsed.path, "", ""))


def _approved_link(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query_keys = frozenset(key.casefold() for key, _value in query)
    decoded_path = unquote(parsed.path).casefold()
    is_linkedin_media_path = (
        hostname is not None
        and _LINKEDIN_HOST.fullmatch(hostname.casefold()) is not None
        and (decoded_path == "/media" or decoded_path.startswith("/media/"))
    )
    if (
        _UNSAFE_URL.search(value) is not None
        or parsed.scheme.casefold() != "https"
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or query_keys & _SENSITIVE_QUERY_KEYS
        or is_linkedin_media_path
        or hostname.casefold() == "licdn.com"
        or hostname.casefold().endswith(".licdn.com")
        or hostname.casefold() == "media.linkedin.com"
        or hostname.casefold().endswith(".media.linkedin.com")
    ):
        return None
    retained = sorted(
        (key, query_value)
        for key, query_value in query
        if key.casefold() not in _TRACKING_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    )
    return urlunsplit(
        ("https", hostname.casefold(), parsed.path, urlencode(retained), "")
    )


def _link_candidates(value: JsonValue) -> tuple[str, ...]:
    match value:
        case str() as link:
            return (link,)
        case list() as values:
            return tuple(link for item in values for link in _link_candidates(item))
        case dict() as mapping:
            match mapping.get("url"):
                case str() as link:
                    return (link,)
                case bool() | int() | float() | list() | dict() | None:
                    return ()
        case bool() | int() | float() | None:
            return ()
    assert_never(value)


def canonical_links(post: BrightDataPost) -> tuple[str, ...]:
    """Filter, normalize, deduplicate, and sort canonical public links."""
    return tuple(
        sorted(
            {
                approved
                for candidate in _link_candidates(post.embedded_links)
                if (approved := _approved_link(candidate)) is not None
            }
        )
    )
