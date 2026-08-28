"""Runtime-specific model discovery and Worker allowlist projection.

The persisted ``models`` field is an operator allowlist.  Each selected agent
runtime is probed through its own adapter and only the intersection is sent to
the Host as an executable ``(runtime, provider, model)`` promise.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

from worker.binary_resolution import resolve_binary

DISCOVERY_TIMEOUT_SECONDS = 20


def discover_effective_models(
    config: dict[str, Any],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    environment = {
        **os.environ,
        **{str(key): str(value) for key, value in config.get("environment", {}).items()},
    }
    allowlist = list(config.get("models") or [])
    effective: list[dict[str, str]] = []
    errors: dict[str, str] = {}
    for runtime in config.get("runtimes") or []:
        try:
            discovered = _discover(str(runtime), environment)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors[str(runtime)] = str(exc)
            continue
        allowed = {
            (str(item["provider"]), str(item["model"]))
            for item in allowlist
            if str(item.get("runtime") or runtime) == runtime
        }
        runtime_has_allowlist = any(
            str(item.get("runtime") or runtime) == runtime for item in allowlist
        )
        for provider, model in discovered:
            if not runtime_has_allowlist or (provider, model) in allowed:
                effective.append({"runtime": str(runtime), "provider": provider, "model": model})
    effective.sort(key=lambda item: (item["runtime"], item["provider"], item["model"]))
    return effective, errors


def _discover(runtime: str, environment: dict[str, str]) -> list[tuple[str, str]]:
    adapters: dict[str, Callable[[str, dict[str, str]], list[tuple[str, str]]]] = {
        "velites": _discover_velites,
        "pi": _discover_pi,
        "openclaw": _discover_openclaw,
    }
    adapter = adapters.get(runtime)
    if adapter is None:
        raise ValueError(f"runtime {runtime!r} has no model discovery adapter")
    binary = resolve_binary(runtime)
    if binary is None:
        raise ValueError(f"runtime {runtime!r} binary is not resolvable")
    return adapter(binary, environment)


def _run(command: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        timeout=DISCOVERY_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise ValueError(f"{' '.join(command[:3])} failed ({result.returncode}): {detail}")
    return result.stdout


def _discover_velites(binary: str, environment: dict[str, str]) -> list[tuple[str, str]]:
    value = json.loads(_run([binary, "models", "list", "--json"], environment))
    if not isinstance(value, list):
        raise ValueError("velites model discovery must return a JSON array")
    return _normalized_pairs(value)


def _discover_openclaw(binary: str, environment: dict[str, str]) -> list[tuple[str, str]]:
    value = json.loads(_run([binary, "models", "list", "--json"], environment))
    rows = value.get("models") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise ValueError("openclaw model discovery has no models array")
    pairs = []
    for row in rows:
        if not isinstance(row, dict) or row.get("available") is False or row.get("missing") is True:
            continue
        key = str(row.get("key") or "")
        provider, separator, model = key.partition("/")
        if separator and provider and model:
            pairs.append((provider, model))
    return sorted(set(pairs))


def _discover_pi(binary: str, environment: dict[str, str]) -> list[tuple[str, str]]:
    # Pi currently has a discovery command but no JSON switch. Keep its text
    # dialect isolated here; the Worker core never knows the output format.
    env = {**environment, "PI_OFFLINE": environment.get("PI_OFFLINE", "1")}
    return parse_pi_model_list(_run([binary, "--list-models"], env))


def parse_pi_model_list(output: str) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("provider ", "no models ")):
            continue
        first, *rest = line.split()
        provider, separator, model = first.partition("/")
        if not separator and rest:
            provider, model = first, rest[0]
        if provider and model and provider.lower() != "provider" and model.lower() != "model":
            pairs.add((provider, model))
    return sorted(pairs)


def _normalized_pairs(rows: list[Any]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("model discovery entries must be objects")
        provider = str(row.get("provider") or "").strip()
        model = str(row.get("model") or "").strip()
        if not provider or not model:
            raise ValueError("model discovery entries require provider and model")
        pairs.add((provider, model))
    return sorted(pairs)
