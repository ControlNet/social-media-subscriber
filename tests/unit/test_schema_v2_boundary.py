import pytest
from pydantic import ValidationError

from social_media_subscriber.domain.post import Post
from tests.unit.test_domain_account import _account
from tests.unit.test_domain_post import _post


def test_post_boundary_requires_explicit_schema_version() -> None:
    # Given
    values = _post(_account().id).model_dump()
    del values["schema_version"]

    # When / Then
    with pytest.raises(ValidationError) as captured:
        _ = Post.model_validate(values)
    assert captured.value.errors(include_input=False)[0]["loc"] == ("schema_version",)
