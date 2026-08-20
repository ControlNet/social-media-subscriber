"""Frozen canonical Post boundary and stable-content hashing."""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Final, Literal, Self, override
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from social_media_subscriber.domain.ids import (
    AccountId,
    ContentHash,
    PlatformPostId,
    PostId,
    post_id_for,
)
from social_media_subscriber.domain.time import canonical_utc

_SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "auth", "key", "password", "signature", "token"}
)
_POST_URL_ERROR_CODE: Final = "canonical_post_url"
_POST_URL_ERROR_MESSAGE: Final = "value must be a canonical public LinkedIn post URL"
_LINK_ERROR_CODE: Final = "approved_public_link"
_LINK_ERROR_MESSAGE: Final = "value must be an approved public HTTPS link"
_POST_ID_ERROR_CODE: Final = "post_id_mismatch"
_POST_ID_ERROR_MESSAGE: Final = "post id does not match platform post id"
_CONTENT_HASH_ERROR_CODE: Final = "content_hash_mismatch"
_CONTENT_HASH_ERROR_MESSAGE: Final = "content hash does not match stable post fields"


class PostKind(StrEnum):
    """Canonical Post variants supported in schema version one."""

    ORIGINAL = "original"


@dataclass(frozen=True, slots=True)
class PostMergeConflictError(Exception):
    """A rediscovered Post conflicts with the immutable canonical record."""

    post_id: PostId
    existing_hash: ContentHash
    candidate_hash: ContentHash

    @override
    def __str__(self) -> str:
        """Return an actionable identifier and both conflicting hashes."""
        return (
            f"post {self.post_id} conflicts: "
            f"{self.existing_hash} != {self.candidate_hash}"
        )


def _canonical_post_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        port = -1
        parsed = urlsplit("")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.linkedin.com"
        or parsed.hostname != "www.linkedin.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(("/posts/", "/feed/update/"))
    ):
        raise PydanticCustomError(
            _POST_URL_ERROR_CODE,
            _POST_URL_ERROR_MESSAGE,
        )
    return value


def _approved_link(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        port = -1
        parsed = urlsplit("")
    query_keys = frozenset(key.casefold() for key, _value in parse_qsl(parsed.query))
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
        or query_keys & _SENSITIVE_QUERY_KEYS
    ):
        raise PydanticCustomError(
            _LINK_ERROR_CODE,
            _LINK_ERROR_MESSAGE,
        )
    return value


@dataclass(frozen=True, slots=True)
class StablePostContent:
    """Fields whose canonical values determine a Post content hash."""

    schema_version: Literal[1]
    id: PostId
    platform_post_id: PlatformPostId
    account_id: AccountId
    canonical_url: str
    published_at: datetime
    text: str | None
    kind: Literal[PostKind.ORIGINAL]
    hashtags: tuple[str, ...]
    links: tuple[str, ...]

    def normalized(self) -> Self:
        """Return the deterministic representation used by the boundary model."""
        normalized_text = None
        if self.text is not None:
            candidate = self.text.replace("\r\n", "\n").replace("\r", "\n").strip()
            normalized_text = candidate or None
        return replace(
            self,
            canonical_url=_canonical_post_url(self.canonical_url),
            published_at=canonical_utc(self.published_at),
            text=normalized_text,
            hashtags=tuple(sorted(set(self.hashtags))),
            links=tuple(sorted({_approved_link(value) for value in self.links})),
        )


def content_hash_for(content: StablePostContent) -> ContentHash:
    """Hash only normalized stable content, excluding discovery time."""
    normalized = content.normalized()
    fields = {
        "account_id": normalized.account_id,
        "canonical_url": normalized.canonical_url,
        "hashtags": normalized.hashtags,
        "id": normalized.id,
        "kind": normalized.kind,
        "links": normalized.links,
        "platform_post_id": normalized.platform_post_id,
        "published_at": normalized.published_at.isoformat().replace("+00:00", "Z"),
        "schema_version": normalized.schema_version,
        "text": normalized.text,
    }
    encoded = json.dumps(
        fields,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ContentHash(hashlib.sha256(encoded).hexdigest())


class Post(BaseModel):
    """Versioned provider-neutral original Post persisted at the boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    schema_version: Literal[1] = 1
    id: PostId = Field(pattern=r"^linkedin:post:.+$")
    platform_post_id: PlatformPostId = Field(min_length=1)
    account_id: AccountId = Field(pattern=r"^linkedin:(?:person|company):.+$")
    canonical_url: str
    published_at: datetime
    text: str | None
    kind: Literal[PostKind.ORIGINAL]
    hashtags: tuple[str, ...] = Field(json_schema_extra={"uniqueItems": True})
    links: tuple[str, ...] = Field(json_schema_extra={"uniqueItems": True})
    first_seen_at: datetime
    content_hash: ContentHash = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_stable(cls, content: StablePostContent, first_seen_at: datetime) -> Self:
        """Construct a canonical Post and compute its stable content hash."""
        normalized = content.normalized()
        return cls(
            schema_version=normalized.schema_version,
            id=normalized.id,
            platform_post_id=normalized.platform_post_id,
            account_id=normalized.account_id,
            canonical_url=normalized.canonical_url,
            published_at=normalized.published_at,
            text=normalized.text,
            kind=normalized.kind,
            hashtags=normalized.hashtags,
            links=normalized.links,
            first_seen_at=first_seen_at,
            content_hash=content_hash_for(normalized),
        )

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical_url(cls, value: str) -> str:
        """Reject unsafe or non-canonical public post URLs."""
        return _canonical_post_url(value)

    @field_validator("published_at", "first_seen_at")
    @classmethod
    def validate_utc_timestamp(cls, value: datetime) -> datetime:
        """Require canonical UTC publication and discovery timestamps."""
        return canonical_utc(value)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        """Normalize newlines and surrounding whitespace without inventing text."""
        if value is None:
            return None
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        return normalized or None

    @field_validator("hashtags")
    @classmethod
    def normalize_hashtags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Deduplicate and sort canonical hashtags."""
        return tuple(sorted(set(values)))

    @field_validator("links")
    @classmethod
    def normalize_links(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Validate, deduplicate, and sort approved public links."""
        return tuple(sorted({_approved_link(value) for value in values}))

    @model_validator(mode="after")
    def validate_identity_and_hash(self) -> Self:
        """Require identity and hash fields to match canonical stable content."""
        if self.id != post_id_for(self.platform_post_id):
            raise PydanticCustomError(
                _POST_ID_ERROR_CODE,
                _POST_ID_ERROR_MESSAGE,
            )
        stable = StablePostContent(
            schema_version=self.schema_version,
            id=self.id,
            platform_post_id=self.platform_post_id,
            account_id=self.account_id,
            canonical_url=self.canonical_url,
            published_at=self.published_at,
            text=self.text,
            kind=self.kind,
            hashtags=self.hashtags,
            links=self.links,
        )
        if self.content_hash != content_hash_for(stable):
            raise PydanticCustomError(
                _CONTENT_HASH_ERROR_CODE,
                _CONTENT_HASH_ERROR_MESSAGE,
            )
        return self


def merge_post(existing: Post, candidate: Post) -> Post:
    """Preserve the first canonical record or reject immutable-content drift."""
    if existing.id == candidate.id and existing.content_hash == candidate.content_hash:
        return existing
    raise PostMergeConflictError(
        post_id=existing.id,
        existing_hash=existing.content_hash,
        candidate_hash=candidate.content_hash,
    )
