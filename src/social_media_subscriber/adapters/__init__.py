from social_media_subscriber.adapters.metadata import AdapterMetadata, adapter
from social_media_subscriber.adapters.metadata_errors import MetadataViolation
from social_media_subscriber.adapters.operations import AdapterOperation
from social_media_subscriber.adapters.protocol import AdapterDriver
from social_media_subscriber.adapters.registry import (
    AdapterRegistry,
    AdapterResolution,
    ResolvedAdapterDrivers,
    UnsupportedAdapterCapability,
)
from social_media_subscriber.adapters.registry_errors import (
    DuplicateAdapterDescriptorError,
    DuplicateAdapterDriverError,
    InvalidAdapterMetadataError,
    MissingAdapterMetadataError,
)

__all__ = [
    "AdapterDriver",
    "AdapterMetadata",
    "AdapterOperation",
    "AdapterRegistry",
    "AdapterResolution",
    "DuplicateAdapterDescriptorError",
    "DuplicateAdapterDriverError",
    "InvalidAdapterMetadataError",
    "MetadataViolation",
    "MissingAdapterMetadataError",
    "ResolvedAdapterDrivers",
    "UnsupportedAdapterCapability",
    "adapter",
]
