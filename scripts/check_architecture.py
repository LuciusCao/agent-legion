"""Architecture contract entry point."""
# ruff: noqa: E402

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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
