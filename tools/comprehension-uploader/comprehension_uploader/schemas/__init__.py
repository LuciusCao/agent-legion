"""Comprehension info schema definitions and registry."""

from __future__ import annotations

from comprehension_uploader.schemas.registry import (
    SUPPORTED_VERSIONS,
    SchemaValidationError,
    UnsupportedSchemaVersionError,
    validate,
)

__all__ = [
    "SUPPORTED_VERSIONS",
    "SchemaValidationError",
    "UnsupportedSchemaVersionError",
    "validate",
]
