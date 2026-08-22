from __future__ import annotations

import pytest

from social_media_subscriber.accounts.errors import AccountInputError
from social_media_subscriber.accounts.locator import parse_linkedin_locator


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/synthetic%ZZ/",
        "https://www.linkedin.com/in/synthetic%FF/",
        "https://www.linkedin.com/in/synthetic%F0%28%8C%28/",
        "https://www.linkedin.com/company/synthetic%E9%9B%AA/",
    ],
)
def test_account_locator_rejects_percent_encoded_slug_variants(url: str) -> None:
    with pytest.raises(AccountInputError):
        _ = parse_linkedin_locator(url)
