"""Bright Data successful-array boundary parsing."""

from typing import Final

from pydantic import ConfigDict, TypeAdapter, ValidationError

from social_media_subscriber.providers.brightdata.errors import (
    BrightDataError,
    BrightDataErrorCategory,
)
from social_media_subscriber.providers.brightdata.models import JsonValue

_JSON_OBJECTS: Final[TypeAdapter[list[dict[str, JsonValue]]]] = TypeAdapter(
    list[dict[str, JsonValue]], config=ConfigDict(strict=True)
)


def parse_items[ModelT](
    values: list[JsonValue],
    item_type: type[ModelT],
    *,
    snapshot_accepted: bool,
) -> tuple[ModelT, ...]:
    """Parse successful records while separating include-error records."""
    adapter = TypeAdapter(tuple[item_type, ...])
    try:
        objects = _JSON_OBJECTS.validate_python(values)
        if any("error" in item for item in objects):
            raise BrightDataError(
                BrightDataErrorCategory.INPUT,
                snapshot_accepted=snapshot_accepted,
            )
        return adapter.validate_python(objects)
    except ValidationError:
        raise BrightDataError(
            BrightDataErrorCategory.SCHEMA,
            snapshot_accepted=snapshot_accepted,
        ) from None
