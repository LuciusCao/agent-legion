#!/usr/bin/env python3
"""Batch soft-delete or restore comprehension info records by fingerprint."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml
from comprehension_uploader.auth import get_token
from comprehension_uploader.config import Config


def load_config(path: str) -> Config:
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("config file must be a mapping")
    return Config.model_validate(raw)


def call_delete(api_base_url: str, token: str, fingerprint: str, timeout: int) -> dict[str, Any]:
    response = requests.post(
        f"{api_base_url.rstrip('/')}/v1/delComprehensionInfo",
        json={"fingerprint": fingerprint},
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def call_restore(api_base_url: str, token: str, fingerprint: str, timeout: int) -> dict[str, Any]:
    response = requests.post(
        f"{api_base_url.rstrip('/')}/v1/updateComprehensionInfo",
        json={"fingerprint": fingerprint, "status": 1},
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch delete or restore comprehension info by fingerprint"
    )
    parser.add_argument("--config", required=True, help="path to config yaml")
    parser.add_argument("--fingerprints", required=True, help="file with one fingerprint per line")
    parser.add_argument(
        "--mode",
        choices=["delete", "restore"],
        default="delete",
        help="delete=soft-delete, restore=set status=1",
    )
    parser.add_argument("--delay", type=float, default=0.05, help="seconds between requests")
    parser.add_argument("--max-retries", type=int, default=3, help="retries per fingerprint")
    args = parser.parse_args()

    config = load_config(args.config)
    token = get_token(config.model_dump())

    fingerprints = [
        line.strip() for line in Path(args.fingerprints).read_text().splitlines() if line.strip()
    ]
    print(f"Loaded {len(fingerprints)} fingerprints, mode={args.mode}")

    stats = {
        "success": 0,
        "not_found": 0,
        "param_error": 0,
        "server_error": 0,
        "other": 0,
        "network_error": 0,
    }
    summary_path = Path("data/delete_batch_summary.json")
    summary: list[dict[str, Any]] = []

    caller = call_restore if args.mode == "restore" else call_delete

    for idx, fingerprint in enumerate(fingerprints, 1):
        result: dict[str, Any] = {
            "fingerprint": fingerprint,
            "ok": False,
            "code": None,
            "message": None,
        }
        for attempt in range(args.max_retries):
            try:
                resp = caller(config.api_base_url, token, fingerprint, config.request_timeout)
                result["ok"] = resp.get("code") == 0
                result["code"] = resp.get("code")
                result["message"] = resp.get("message")
                result["data"] = resp.get("data")
                break
            except requests.RequestException as exc:
                result["message"] = str(exc)
                if attempt == args.max_retries - 1:
                    result["code"] = "NETWORK_ERROR"
                else:
                    time.sleep(0.5 * (attempt + 1))

        code = result.get("code")
        if result["ok"]:
            stats["success"] += 1
        elif code == 11053:
            stats["not_found"] += 1
        elif code == 10011:
            stats["param_error"] += 1
        elif code == 10998:
            stats["server_error"] += 1
        elif code == "NETWORK_ERROR":
            stats["network_error"] += 1
        else:
            stats["other"] += 1

        summary.append(result)
        if idx % 100 == 0:
            print(f"  progress {idx}/{len(fingerprints)}: {stats}")
        time.sleep(args.delay)

    summary_path.write_text(
        json.dumps({"stats": stats, "details": summary}, ensure_ascii=False, indent=2)
    )
    print(f"Done. Stats: {stats}")
    print(f"Summary written to {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
