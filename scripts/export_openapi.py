import argparse
import inspect
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

from scripts.architecture.route_contracts import has_protocol_response_endpoint
from server.app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def validate_response_contracts(schema: dict[str, Any], exempt_operation_ids: set[str]) -> None:
    errors = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId", f"{method} {path}")
            if operation_id in exempt_operation_ids:
                continue
            for status, response in operation.get("responses", {}).items():
                if not str(status).startswith("2"):
                    continue
                content = response.get("content", {})
                json_schema = content.get("application/json", {}).get("schema")
                if json_schema is not None and "$ref" not in json_schema:
                    errors.append(f"{operation_id} has inline JSON response schema")
    if errors:
        raise ValueError("; ".join(errors))


def validate_unique_api_routes(app: Any) -> None:
    seen: set[tuple[str, str]] = set()
    duplicates: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for method in getattr(route, "methods", set()):
            key = (method, path)
            if key in seen:
                duplicates.append(f"{method} {path}")
            seen.add(key)
    if duplicates:
        raise ValueError(
            "; ".join(f"duplicate API route {operation}" for operation in sorted(duplicates))
        )


def _endpoint_exemption_key(endpoint: Any) -> str | None:
    try:
        source_path = Path(inspect.getfile(endpoint)).resolve()
        relative_path = source_path.relative_to(PROJECT_ROOT)
    except (TypeError, ValueError):
        return None
    return f"{relative_path.as_posix()}:{getattr(endpoint, '__name__', '')}"


def response_contract_exempt_operation_ids(app: Any, exempt_route_handlers: set[str]) -> set[str]:
    operation_ids: set[str] = set()
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if _endpoint_exemption_key(endpoint) not in exempt_route_handlers and not (
            callable(endpoint) and has_protocol_response_endpoint(endpoint)
        ):
            continue
        operation_id = getattr(route, "unique_id", "")
        if operation_id:
            operation_ids.add(operation_id)
    return operation_ids


def build_openapi_schema(data_dir: Path) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(data_dir=data_dir, start_worker=False)
    workflows = app.state.settings.config.setdefault("workflows", {})
    workflows["enabled"] = True
    validate_unique_api_routes(app)
    schema: dict[str, Any] = deepcopy(app.openapi())
    schema["paths"] = {
        path: definition
        for path, definition in schema.get("paths", {}).items()
        if path.startswith("/api")
    }
    exemptions = yaml.safe_load(
        (PROJECT_ROOT / "config/architecture/architecture-exemptions.yaml").read_text(
            encoding="utf-8"
        )
    ) or {"exemptions": []}
    exempt_route_handlers = {
        ex["path"]
        for ex in exemptions.get("exemptions", [])
        if ex.get("check") == "architecture.route_response_model"
    }
    exempt_operation_ids = response_contract_exempt_operation_ids(app, exempt_route_handlers)
    validate_response_contracts(schema, exempt_operation_ids)
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the Agent Legion OpenAPI schema")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with TemporaryDirectory() as temporary_directory:
        schema = build_openapi_schema(Path(temporary_directory))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
