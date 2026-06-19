from server.app.db.migrations.models import Migration


def _apply(conn) -> None:
    # Text columns need no DDL; this records application support for relative values.
    return None


MIGRATION = Migration(version=9, name="relative_path_storage", apply=_apply)
