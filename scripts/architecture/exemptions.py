"""Architecture exemption loading and categorization."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path


def load_exemptions(root: Path) -> tuple:
    """Load governed architecture exemptions from the YAML registry."""
    from server.app.quality.exemptions import load_exemptions as _load

    path = root / "config/architecture/architecture-exemptions.yaml"
    if not path.exists():
        return ()
    return _load(path)


def categorize_exemptions(exemptions: tuple):
    """Group exemptions by check name for efficient lookup."""
    response_model_exemptions: set[str] = set()
    annotation_exemptions: set[str] = set()
    route_import_exempt_files: set[str] = set()
    route_import_exempt_modules: dict[str, set[str]] = defaultdict(set)
    scheduler_import_exempt_files: set[str] = set()
    scheduler_import_exempt_modules: dict[str, set[str]] = defaultdict(set)
    scheduler_threadpool_exempt_targets: dict[str, set[str]] = defaultdict(set)
    file_budget_exemptions: set[str] = set()
    for ex in exemptions:
        if ex.check == "architecture.route_response_model":
            response_model_exemptions.add(ex.path)
        elif ex.check == "architecture.route_annotation_any":
            annotation_exemptions.add(ex.path)
        elif ex.check == "architecture.route_import_boundary":
            file_part, _, module_part = ex.path.partition(":")
            if module_part:
                route_import_exempt_modules[file_part].add(module_part)
            else:
                route_import_exempt_files.add(file_part)
        elif ex.check == "architecture.scheduler_import_boundary":
            file_part, _, module_part = ex.path.partition(":")
            if module_part:
                scheduler_import_exempt_modules[file_part].add(module_part)
            else:
                scheduler_import_exempt_files.add(file_part)
        elif ex.check == "architecture.scheduler_threadpool":
            file_part, _, target = ex.path.partition(":")
            if target:
                scheduler_threadpool_exempt_targets[file_part].add(target)
        elif ex.check == "architecture.file_budget":
            file_budget_exemptions.add(ex.path)
    return (
        response_model_exemptions,
        annotation_exemptions,
        route_import_exempt_files,
        route_import_exempt_modules,
        scheduler_import_exempt_files,
        scheduler_import_exempt_modules,
        scheduler_threadpool_exempt_targets,
        file_budget_exemptions,
    )


__all__ = ["load_exemptions", "categorize_exemptions"]
