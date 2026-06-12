import argparse
import json
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from server.app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def validate_response_contracts(schema: dict[str, Any], exempt_operation_names: set[str]) -> None:
    errors = []
    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId", f"{method} {path}")
            if any(operation_id.startswith(f"{name}_") for name in exempt_operation_names):
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


def build_openapi_schema(data_dir: Path) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(data_dir=data_dir, start_worker=False)
    pipelines = app.state.settings.config.setdefault("pipelines", {})
    pipelines["enabled"] = True
    schema = deepcopy(app.openapi())
    schema["paths"] = {
        path: definition
        for path, definition in schema.get("paths", {}).items()
        if path.startswith("/api")
    }
    architecture_config = json.loads(
        (PROJECT_ROOT / "config/architecture-budgets.json").read_text(encoding="utf-8")
    )
    exempt_operation_names = {
        key.rsplit(":", 1)[-1] for key in architecture_config.get("route_exemptions", [])
    }
    validate_response_contracts(schema, exempt_operation_names)
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the Video Hive OpenAPI schema")
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
