import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from server.app.main import create_app


def build_openapi_schema(data_dir: Path) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(data_dir=data_dir, start_worker=False)
    pipelines = app.state.settings.config.setdefault("pipelines", {})
    pipelines["enabled"] = True
    return app.openapi()


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
