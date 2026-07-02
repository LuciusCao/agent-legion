from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from comprehension_uploader.api_client import ComprehensionAPIClient
from comprehension_uploader.config import Config, ConfigError
from comprehension_uploader.db import Database
from comprehension_uploader.package_parser import (
    PackageParseError,
    parse_package,
    validate_package,
)
from comprehension_uploader.packager import package_comprehension_info
from comprehension_uploader.question_source import build_question_source
from comprehension_uploader.scanner import Scanner
from comprehension_uploader.uploader import Uploader


def _load_config(path: str) -> Config:
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ConfigError("config file must be a mapping")
    return Config.model_validate(raw)


def cmd_init_db(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    db = Database(config.db_path)
    db.init_schema()
    print(f"Initialized database at {config.db_path}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    db = Database(config.db_path)
    db.init_schema()
    api = ComprehensionAPIClient(config)
    uploader = Uploader(config, db, api)

    workspace_id = args.workspace
    if args.batch_id:
        batch_id = args.batch_id
    elif workspace_id:
        batch_id = f"{workspace_id}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"  # noqa: UP017
    else:
        print("Either --workspace or --batch-id is required", file=sys.stderr)
        return 2

    try:
        records = list(parse_package(Path(args.package)))
    except PackageParseError as exc:
        print(f"Parse error: {exc}", file=sys.stderr)
        return 2

    uploader.upload_batch(records, batch_id, workspace_id=workspace_id)
    print(f"Uploaded batch {batch_id} ({len(records)} records)")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    db = Database(config.db_path)
    db.init_schema()
    source = build_question_source(config)
    scanner = Scanner(config, db, source)
    summary = scanner.scan(args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    db = Database(config.db_path)
    state = db.states.get(args.question_id)
    logs = db.logs.get_logs(args.question_id)
    output: dict[str, Any] = {
        "state": dict(state) if state else None,
        "logs": [dict(row) for row in logs],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    source = build_question_source(config)
    summary = package_comprehension_info(
        input_dir=Path(args.input_dir),
        output_path=Path(args.output),
        question_source=source,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    package_path = Path(args.package)
    passed, failed, errors = validate_package(package_path)
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    print(f"Validated {package_path}: passed={passed}, failed={failed}")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="comprehension-uploader")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init-db", help="Initialize the SQLite schema")
    init_parser.add_argument("--config", required=True, help="Path to YAML config")
    init_parser.set_defaults(func=cmd_init_db)

    upload_parser = sub.add_parser("upload", help="Upload a package.jsonl batch")
    upload_parser.add_argument("--config", required=True, help="Path to YAML config")
    upload_parser.add_argument(
        "--workspace",
        help="Workspace identifier; stored in logs and used to generate batch-id if --batch-id is omitted",
    )
    upload_parser.add_argument(
        "--batch-id", help="Batch identifier (defaults to workspace-<timestamp>)"
    )
    upload_parser.add_argument("package", help="Path to package.jsonl")
    upload_parser.set_defaults(func=cmd_upload)

    scan_parser = sub.add_parser("scan", help="Scan for stale questions")
    scan_parser.add_argument("--config", required=True, help="Path to YAML config")
    scan_parser.add_argument("--output", help="Path to write stale list JSON")
    scan_parser.set_defaults(func=cmd_scan)

    status_parser = sub.add_parser("status", help="Show status for a question")
    status_parser.add_argument("--config", required=True, help="Path to YAML config")
    status_parser.add_argument("question_id")
    status_parser.set_defaults(func=cmd_status)

    package_parser_cmd = sub.add_parser(
        "package", help="Build a package.jsonl from comprehension_info.json files"
    )
    package_parser_cmd.add_argument("--config", required=True, help="Path to YAML config")
    package_parser_cmd.add_argument(
        "--input-dir", required=True, help="Directory containing comprehension_info.json files"
    )
    package_parser_cmd.add_argument("--output", required=True, help="Path to write package.jsonl")
    package_parser_cmd.set_defaults(func=cmd_package)

    validate_parser_cmd = sub.add_parser(
        "validate", help="Validate a package.jsonl without uploading"
    )
    validate_parser_cmd.add_argument("package", help="Path to package.jsonl")
    validate_parser_cmd.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except PackageParseError as exc:
        print(f"Parse error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
