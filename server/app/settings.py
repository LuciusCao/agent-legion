from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Settings:
    root_dir: Path
    data_dir: Path
    videos_dir: Path
    logs_dir: Path
    packages_dir: Path
    config: dict[str, Any]


def load_settings(data_dir: Path | None = None, config_path: Path | None = None) -> Settings:
    root_dir = Path(__file__).resolve().parents[2]
    config_file = config_path or root_dir / "config" / "pipeline.yaml"
    config: dict[str, Any] = {}
    if config_file.exists():
        loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config = loaded
    resolved_data_dir = data_dir or root_dir / str(config.get("data_dir", "data"))
    videos_dir = resolved_data_dir / "videos"
    logs_dir = resolved_data_dir / "logs"
    packages_dir = resolved_data_dir / "packages"
    for path in [resolved_data_dir, videos_dir, logs_dir, packages_dir]:
        path.mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=root_dir,
        data_dir=resolved_data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        config=config,
    )

