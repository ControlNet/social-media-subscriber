from __future__ import annotations

from typer.testing import CliRunner

from social_media_subscriber.cli import create_app
from tests.unit.test_cli import CanaryProviderError, FakeApplication


def test_ci_exception_log_preserves_context_and_redacts_secret_url() -> None:
    # Given
    application = FakeApplication(
        publish_error=CanaryProviderError(
            "provider canary-secret failed at https://canary.invalid/private"
        )
    )

    # When
    result = CliRunner().invoke(
        create_app(application),
        ["publish-dist", "--snapshot", "candidate", "--expected-sha", "absent"],
        env={
            "CI": "true",
            "ACCOUNTS": "https://canary.invalid/private",
            "BRIGHT_DATA_API_KEYS": "canary-secret",
        },
    )

    # Then
    assert result.exit_code == 6
    log = next(
        line for line in result.output.splitlines() if '"event":"cli.failure"' in line
    )
    assert '"error_type":"CanaryProviderError"' in log
    assert '"category":"unhandled"' in log
    assert '"stack":"' in log
    assert "[REDACTED]" in log
    assert "canary-secret" not in result.output
    assert "https://canary.invalid" not in result.output
