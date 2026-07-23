#!/usr/bin/env python3
"""Warn about architecture exemptions whose removal condition is overdue."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from server.app.quality.exemption_age import exemption_age_warnings
from server.app.quality.exemptions import load_exemptions

project_root = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report architecture exemptions older than the age limit."
    )
    parser.add_argument(
        "--exemptions",
        default="config/architecture/architecture-exemptions.yaml",
        help="Path to the exemption registry YAML file.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="Age in days beyond which an exemption is reported.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    exemptions_path = Path(args.exemptions)
    if not exemptions_path.exists():
        logger.error("exemptions file not found: %s", exemptions_path)
        return 1

    exemptions = load_exemptions(exemptions_path)
    warnings = exemption_age_warnings(
        exemptions, base_path=project_root, max_age_days=args.max_age_days
    )

    for warning in warnings:
        logger.warning("%s", warning)

    if warnings:
        logger.warning(
            "%d exemption(s) exceed the %d-day age limit",
            len(warnings),
            args.max_age_days,
        )
        return 1

    logger.info("OK: no exemption exceeds the %d-day age limit", args.max_age_days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
