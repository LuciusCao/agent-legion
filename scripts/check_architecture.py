"""Architecture contract entry point."""

from scripts.architecture.helpers import (
    forbidden_imports,
    imported_modules,
    is_scheduler_path,
)
from scripts.architecture.main import main
from scripts.architecture.repository import check_repository

__all__ = [
    "check_repository",
    "forbidden_imports",
    "imported_modules",
    "is_scheduler_path",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
