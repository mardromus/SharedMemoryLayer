"""Python SDK for the Shared Memory Layer for Cross-Team AI Agents."""
from shared_memory.client import MemoryClient, Record, SearchResults
from shared_memory.errors import (
    AuthenticationError,
    ConflictError,
    MemoryAPIError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ValidationError,
)

__version__ = "1.1.0"

__all__ = [
    "AuthenticationError",
    "ConflictError",
    "MemoryAPIError",
    "MemoryClient",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "Record",
    "SearchResults",
    "ServerError",
    "ValidationError",
]
