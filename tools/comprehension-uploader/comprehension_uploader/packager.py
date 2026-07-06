"""Package comprehension_info.json artifacts into a package.jsonl file."""

from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from comprehension_uploader.question_source import QuestionSource

logger = logging.getLogger(__name__)


def package_comprehension_info(
    input_dir: Path,
    output_path: Path,
    question_source: QuestionSource,
) -> dict[str, Any]:
    """Read comprehension_info.json files under ``input_dir`` and write package.jsonl.

    Each output line contains the fields expected by ``UploadRecord``:
    ``question_id``, ``subject_id``, ``question_uuid``, ``question_vno``,
    ``comprehension_difficulty``, ``format_vno``, ``comprehension_data``,
    ``stem`` and ``options``.

    The top-level ``schema_version`` from ``comprehension_info.json`` is written
    as ``format_vno``.  If it is missing, ``"v1"`` is used and a warning is
    emitted.
    """
    input_dir = Path(input_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    info_paths = sorted(input_dir.rglob("comprehension_info.json"))
    written = 0
    skipped = 0

    with output_path.open("w", encoding="utf-8") as handle:
        for info_path in info_paths:
            try:
                payload = json.loads(info_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON: %s", info_path)
                skipped += 1
                continue

            if not isinstance(payload, dict):
                logger.warning("Skipping non-object JSON: %s", info_path)
                skipped += 1
                continue

            question_id = payload.get("question_id")
            if not question_id:
                logger.warning("Skipping comprehension_info without question_id: %s", info_path)
                skipped += 1
                continue

            latest = question_source.get_latest(str(question_id))
            if not latest:
                logger.warning(
                    "Skipping %s: question source has no content for %s",
                    info_path,
                    question_id,
                )
                skipped += 1
                continue

            comprehension_data = payload.get("comprehension_data")
            if not isinstance(comprehension_data, dict):
                logger.warning(
                    "Skipping %s: comprehension_data is not an object",
                    info_path,
                )
                skipped += 1
                continue

            schema_version = payload.get("schema_version")
            if not schema_version:
                logger.warning(
                    "%s missing schema_version, defaulting to v1",
                    info_path,
                )
                schema_version = "v1"
            format_vno = str(schema_version).strip()

            package_line = {
                "question_id": latest.get("question_id") or str(question_id),
                "subject_id": latest.get("subject_id"),
                "question_uuid": latest.get("question_uuid"),
                "question_vno": latest.get("question_vno"),
                "comprehension_difficulty": comprehension_data.get("comprehension_difficulty"),
                "format_vno": format_vno,
                "comprehension_data": comprehension_data,
                "stem": latest.get("stem"),
                "options": latest.get("options"),
            }

            handle.write(json.dumps(package_line, ensure_ascii=False) + "\n")
            written += 1

    summary = {
        "input_dir": str(input_dir),
        "output": str(output_path),
        "found": len(info_paths),
        "written": written,
        "skipped": skipped,
    }
    logger.info(
        "Packaged %d comprehension_info files to %s (skipped %d)",
        written,
        output_path,
        skipped,
    )
    return summary


def package_comprehension_info_from_workspace_zip(
    package_path: Path,
    output_path: Path,
    question_source: QuestionSource,
) -> dict[str, Any]:
    """Build a package.jsonl from a workspace-generated zip of job artifacts.

    The zip is expected to contain a root ``manifest.json`` and one directory
    per job. Each job directory may contain a ``comprehension_info.json`` file,
    which is extracted to a temporary directory and then passed through the
    normal ``package_comprehension_info`` flow.
    """
    package_path = Path(package_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    info_count = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        temp_root = Path(tmp_dir)
        with zipfile.ZipFile(package_path, "r") as zf:
            for member in zf.namelist():
                parts = Path(member).parts
                if len(parts) == 2 and parts[1] == "comprehension_info.json":
                    target = temp_root / parts[0] / "comprehension_info.json"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, target.open("wb") as dst:
                        dst.write(src.read())
                    info_count += 1

        summary = package_comprehension_info(
            input_dir=temp_root,
            output_path=output_path,
            question_source=question_source,
        )

    summary["workspace_package"] = str(package_path)
    summary["comprehension_info_files"] = info_count
    return summary
