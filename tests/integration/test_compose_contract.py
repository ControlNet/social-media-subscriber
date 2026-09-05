"""Validate the editable Compose deployment without starting any service."""

from pathlib import Path

from tests.workflow_helpers import load_workflow, mapping

_ROOT = Path(__file__).parents[2]
_EXAMPLE_ACCOUNTS = (
    "https://www.linkedin.com/in/YOUR_LINKEDIN_PROFILE/,"
    "https://www.linkedin.com/company/YOUR_COMPANY/,"
    "https://x.com/YOUR_X_HANDLE/"
)
_EXAMPLE_SOURCES = "apify:YOUR_APIFY_TOKEN,brightdata:YOUR_BRIGHTDATA_TOKEN"


def test_compose_runs_published_image_with_editable_settings() -> None:
    configuration = load_workflow(_ROOT / "docker-compose.yaml")
    subscriber = mapping(mapping(configuration["services"])["subscriber"])
    assert subscriber["image"] == "controlnet/social-media-subscriber:latest"
    assert subscriber["restart"] == "unless-stopped"
    assert "env_file" not in subscriber
    assert not (_ROOT / "compose.yaml").exists()
    assert "platform" not in subscriber
    assert "build" not in subscriber
    assert subscriber["volumes"] == [
        "./social-media:/data",
        "./state:/state",
        "/etc/localtime:/etc/localtime:ro",
    ]
    assert "volumes" not in configuration
    assert mapping(subscriber["environment"]) == {
        "ACCOUNTS": _EXAMPLE_ACCOUNTS,
        "SOURCES": _EXAMPLE_SOURCES,
        "PUID": "1000",
        "PGID": "1000",
        "CRON_SCHEDULE": "17 3 * * *",
        "ENABLE_MEDIA_COMPRESSION": "true",
        "REFRESH_ON_STARTUP": "true",
        "WORKER_TIMEOUT_SECONDS": "0",
    }
    readme = (_ROOT / "README.md").read_text()
    assert "[docker-compose.yaml](docker-compose.yaml)" in readme
    assert "docker compose pull" in readme
    assert "docker compose up -d" in readme
    assert _EXAMPLE_ACCOUNTS in readme
    assert _EXAMPLE_SOURCES in readme
    assert "Replace every `YOUR_...` value" in readme
