"""Exclusive output-directory ownership and argument validation."""

from __future__ import annotations

import argparse
import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from scripts.stress.worker_resource_identity import ContractViolationError
from scripts.stress.worker_resource_json_budget import bounded_json as bounded_json


def require_empty_output_dir(output_dir: Path) -> None:
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ContractViolationError(
            f"output-dir must be absent or empty; use a new directory or clean it yourself: {output_dir}"
        )


def acquire_output_dir_lock(output_dir: Path) -> int:
    lock_path = output_dir.parent / f".{output_dir.name}.worker-resource-measurement.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise ContractViolationError(f"output-dir is already being measured: {output_dir}") from exc
    return descriptor


@contextmanager
def output_dir_lock(output_dir: Path) -> Iterator[int]:
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    descriptor = acquire_output_dir_lock(output_dir)
    try:
        require_empty_output_dir(output_dir)
        yield descriptor
    finally:
        os.close(descriptor)


def _positive(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed
