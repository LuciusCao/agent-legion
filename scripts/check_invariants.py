#!/usr/bin/env python3
"""Validate the architecture invariant registry without executing evidence targets."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from scripts.quality.exemptions import load_exemptions, validate_exemptions
from scripts.quality.invariants import load_registry, validate_registry

project_root = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the architecture invariant and exemption registries."
    )
    parser.add_argument(
        "--registry",
        default="config/architecture/architecture-invariants.yaml",
        help="Path to the invariant registry YAML file.",
    )
    parser.add_argument(
        "--exemptions",
        default="config/architecture/architecture-exemptions.yaml",
        help="Path to the exemption registry YAML file.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    registry_path = Path(args.registry)
    if not registry_path.exists():
        logger.error("registry file not found: %s", registry_path)
        return 1

    exemptions_path = Path(args.exemptions)
    if not exemptions_path.exists():
        logger.error("exemptions file not found: %s", exemptions_path)
        return 1

    try:
        invariants = load_registry(registry_path)
    except yaml.YAMLError as exc:
        logger.error("YAML parse error: %s", exc)
        return 1

    try:
        exemptions = load_exemptions(exemptions_path)
    except yaml.YAMLError as exc:
        logger.error("YAML parse error: %s", exc)
        return 1

    errors = validate_registry(invariants, base_path=project_root)
    errors.extend(validate_exemptions(exemptions, base_path=project_root))

    if errors:
        for error in errors:
            logger.error("%s", error)
        return 1

    logger.info(
        "OK: %s architecture invariant(s) and %s exemption(s) validated",
        len(invariants),
        len(exemptions),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
