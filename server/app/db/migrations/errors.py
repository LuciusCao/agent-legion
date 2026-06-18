class MigrationError(RuntimeError):
    """Base class for migration failures."""


class MigrationRegistryError(MigrationError):
    """The migration registry itself is invalid."""


class MigrationHistoryError(MigrationError):
    """The recorded migration history is inconsistent with the registry."""
