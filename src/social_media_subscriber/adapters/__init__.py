from social_media_subscriber.adapters.metadata import AdapterMetadata, adapter
from social_media_subscriber.adapters.metadata_errors import MetadataViolation
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.protocol import AdapterDriver
from social_media_subscriber.adapters.registry import AdapterRegistry
from social_media_subscriber.adapters.registry_errors import (
    DuplicateAdapterDriverError,
    InvalidAdapterMetadataError,
    MissingAdapterMetadataError,
)

__all__ = [
    "AdapterDriver",
    "AdapterMetadata",
    "AdapterOperation",
    "AdapterRegistry",
    "DuplicateAdapterDriverError",
    "InvalidAdapterMetadataError",
    "MetadataViolation",
    "MissingAdapterMetadataError",
    "adapter",
]
