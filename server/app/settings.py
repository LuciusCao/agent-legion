import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml


@dataclass
class Settings:
    root_dir: Path
    data_dir: Path
    videos_dir: Path
    logs_dir: Path
    packages_dir: Path
    jobs_dir: Path
    config: dict[str, Any]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def _normalize_cms_config(config: dict[str, Any]) -> None:
    """Derive legacy URL fields from base_url when present."""
    cms = config.get("cms")
    if not isinstance(cms, dict):
        return
    base_url = str(cms.get("base_url", "")).rstrip("/")
    if not base_url:
        return
    params: dict[str, str] = {
        "bank_version": str(cms.get("bank_version", "v5")),
        "country_id": str(cms.get("country_id", "1")),
        "subject_id": str(cms.get("subject_id", "2")),
    }
    if not cms.get("knowledge_url"):
        cms["knowledge_url"] = f"{base_url}/knowledge/detail?" + urlencode(params)
    if not cms.get("question_url"):
        cms["question_url"] = f"{base_url}/question/detail?" + urlencode(params)
    if not cms.get("question_detail_url"):
        cms["question_detail_url"] = cms["question_url"]
    list_params = {**params}
    if "page_size" in cms and cms["page_size"] not in (None, ""):
        list_params["page_size"] = str(cms["page_size"])
    else:
        list_params["page_size"] = "50"
    if not cms.get("question_list_url"):
        cms["question_list_url"] = f"{base_url}/question/list?" + urlencode(list_params)


def load_settings(data_dir: Path | None = None, config_path: Path | None = None) -> Settings:
    root_dir = Path(__file__).resolve().parents[2]
    load_env_file(root_dir / ".env")
    config_file = config_path or root_dir / "config" / "pipeline.yaml"
    config: dict[str, Any] = {}
    if config_file.exists():
        loaded = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config = loaded
    _normalize_cms_config(config)
    resolved_data_dir = data_dir or root_dir / str(config.get("data_dir", "data"))
    videos_dir = resolved_data_dir / "videos"
    logs_dir = resolved_data_dir / "logs"
    packages_dir = resolved_data_dir / "packages"
    jobs_dir = resolved_data_dir / "jobs"
    for path in [resolved_data_dir, videos_dir, logs_dir, packages_dir, jobs_dir]:
        path.mkdir(parents=True, exist_ok=True)
    return Settings(
        root_dir=root_dir,
        data_dir=resolved_data_dir,
        videos_dir=videos_dir,
        logs_dir=logs_dir,
        packages_dir=packages_dir,
        jobs_dir=jobs_dir,
        config=config,
    )
