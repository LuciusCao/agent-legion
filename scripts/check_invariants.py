#!/usr/bin/env python3
"""Validate the architecture invariant registry without executing evidence targets."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Allow running the script directly from the repository root.
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from server.app.quality.invariants import load_registry, validate_registry  # noqa: E402

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the architecture invariant registry.")
    parser.add_argument(
        "--registry",
        default="config/architecture-invariants.yaml",
        help="Path to the invariant registry YAML file.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    registry_path = Path(args.registry)
    if not registry_path.exists():
        logger.error(f"registry file not found: {registry_path}")
        return 1

    invariants = load_registry(registry_path)
    errors = validate_registry(invariants)

    if errors:
        for error in errors:
            logger.error(error)
        return 1

    logger.info(f"OK: {len(invariants)} architecture invariant(s) validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
