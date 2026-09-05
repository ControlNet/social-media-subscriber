"""Executable package entry point."""

from social_media_subscriber.cli import app
from social_media_subscriber.cli_logging import configure_logging

configure_logging()
app()
