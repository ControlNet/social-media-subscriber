"""Sensitive field detection for persistable provider payloads."""

from __future__ import annotations

import re
from typing import Final, assert_never

type JsonValue = (
    bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
)

_FORBIDDEN_FIELD_NAMES: Final = frozenset(
    """accessToken apiKey auth authorization clientSecret cookie cookies
    credential credentials error errors headers httpHeaders password
    rawBody rawResponse request requestHeader requestHeaders requestId requests
    responseHeader responseHeaders secret secrets setCookie snapshotId
    token""".casefold().split()
)
_FORBIDDEN_FIELD_MARKERS: Final = (
    "accesstoken",
    "apikey",
    "authentication",
    "authorization",
    "authinfo",
    "bearer",
    "clientsecret",
    "cookie",
    "credential",
    "header",
    "password",
    "rawbody",
    "rawresponse",
    "secret",
    "setcookie",
    "snapshotid",
    "token",
)
_TRANSPORT_FIELD_TOKENS: Final = frozenset(
    {"error", "exception", "request", "response", "session"}
)
_TRANSPORT_CONTAINER_TOKENS: Final = frozenset(
    {"body", "context", "data", "details", "info", "metadata", "payload"}
)
_FIELD_TOKEN: Final = re.compile(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|\Z)|[A-Z]?[a-z]+|[0-9]+")


def _is_forbidden_field_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    tokens = frozenset(
        match.group(0).casefold() for match in _FIELD_TOKEN.finditer(value)
    )
    return (
        normalized in _FORBIDDEN_FIELD_NAMES
        or normalized.endswith("auth")
        or any(marker in normalized for marker in _FORBIDDEN_FIELD_MARKERS)
        or (
            bool(tokens & _TRANSPORT_FIELD_TOKENS)
            and bool(tokens & _TRANSPORT_CONTAINER_TOKENS)
        )
    )


def contains_forbidden_field(value: JsonValue) -> bool:
    """Return whether nested JSON contains non-persistable metadata."""
    match value:
        case dict() as mapping:
            return any(
                _is_forbidden_field_name(key) or contains_forbidden_field(item)
                for key, item in mapping.items()
            )
        case list() as values:
            return any(contains_forbidden_field(item) for item in values)
        case bool() | int() | float() | str() | None:
            return False
    assert_never(value)
