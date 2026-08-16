"""Demo workflow intake node: expand one knowledge-point markdown into job input.

First node of the ``education_video_problems_generation`` example workflow
(capability ``intake_knowledge_points``).

How job parameters reach this node (the intake fan-out contract):

- The DAG's intake mode (``direct_ids``) fans out one job per input value at
  intake time via the platform's direct resolver
  (``server/app/services/job_intake_resolution.py::resolve_direct_candidates``);
  no external service is consulted.
- The input value becomes the job's ``source_id`` (``create_jobs_bulk`` maps
  the candidate ``entity_id`` onto it). For this workflow the value is a
  knowledge-point file stem, e.g. ``fraction-addition-subtraction``.
- This node maps ``source_id`` to ``<knowledge_dir>/<source_id>.md``, parses
  the markdown, and writes ``knowledge_point.json`` for the downstream agent
  nodes.

``knowledge_dir`` is declared in the capability's config_schema and arrives
via ``ctx.config`` (schema default → node config → workspace override, frozen
at intake). The default is a repo-relative path resolved against the host
root from the runtime (``ctx.root_dir``, injected by the parent executor) —
this works for Host-local execution; on a remote Worker the bundle carries
no ``examples/`` tree, so a Worker-bound deployment must point
``knowledge_dir`` at a path that exists on the Worker host.

This file is the git-reviewed **seed source** of the demo intake node: at
startup it is published as a global node_code version (EXEC-CODE-002, #96)
and executes from the DB text inside the velites sandbox. Pure stdlib + node
SDK: no business imports, no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.node_sdk import NodeContext, entrypoint

DEFAULT_KNOWLEDGE_DIR = "examples/education-video-problems-generation"

_HEADING_1 = "# "
_HEADING_2 = "## "
_META_BULLET = "- "
_CONCEPT_SECTION = "核心概念"
_MISTAKES_SECTION = "常见易错点"


def _resolve_knowledge_dir(ctx: NodeContext) -> Path:
    configured = str(ctx.config.get("knowledge_dir") or DEFAULT_KNOWLEDGE_DIR).strip()
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = (ctx.root_dir or Path.cwd()) / path
    return path


def _available_ids(knowledge_dir: Path) -> list[str]:
    if not knowledge_dir.is_dir():
        return []
    return sorted(path.stem for path in knowledge_dir.glob("*.md"))


def _parse_knowledge_markdown(text: str, source_id: str) -> dict[str, Any]:
    """Parse one knowledge-point markdown into the structured payload.

    Contract (kept in sync with ``examples/education-video-problems-generation/*.md``):
    one ``# <title>`` heading, then ``- <key>：<value>`` metadata bullets,
    then ``## 核心概念`` (free paragraphs) and ``## 常见易错点`` (bullets).
    """
    title = ""
    metadata: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current_section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(_HEADING_2):
            current_section = line[len(_HEADING_2) :].strip()
            sections.setdefault(current_section, [])
            continue
        if line.startswith(_HEADING_1) and not title:
            title = line[len(_HEADING_1) :].strip()
            continue
        if current_section is None:
            # Metadata bullets between the title and the first section.
            if line.startswith(_META_BULLET) and "：" in line:
                key, _, value = line[len(_META_BULLET) :].partition("：")
                metadata[key.strip()] = value.strip()
            continue
        sections.setdefault(current_section, []).append(line)

    if not title:
        raise ValueError(f"知识点文件缺少一级标题（# 知识点名）: {source_id}")

    concept_lines = sections.get(_CONCEPT_SECTION, [])
    summary = "\n\n".join(concept_lines).strip()
    if not summary:
        raise ValueError(f"知识点文件缺少「{_HEADING_2}{_CONCEPT_SECTION}」段落: {source_id}")

    mistakes = [
        line[len(_META_BULLET) :].strip()
        for line in sections.get(_MISTAKES_SECTION, [])
        if line.startswith(_META_BULLET)
    ]
    if not mistakes:
        raise ValueError(f"知识点文件缺少「{_HEADING_2}{_MISTAKES_SECTION}」条目: {source_id}")

    return {
        "id": source_id,
        "title": title,
        "grade": metadata.get("适用年级", ""),
        "subject": metadata.get("学科", ""),
        "summary": summary,
        "common_mistakes": mistakes,
    }


@entrypoint
def run(ctx: NodeContext) -> None:
    log = ctx.logger
    ctx.checkpoint()

    source_id = str(ctx.job["source_id"])
    knowledge_dir = _resolve_knowledge_dir(ctx)
    source_file = knowledge_dir / f"{source_id}.md"
    log.info(
        "example_intake: source_id=%s knowledge_dir=%s",
        source_id,
        knowledge_dir,
    )
    if not source_file.is_file():
        available = _available_ids(knowledge_dir)
        raise RuntimeError(
            f"知识点文件不存在: {source_file}"
            f"（可用知识点: {', '.join(available) if available else '无——请确认 knowledge_dir 配置'}）"
        )

    knowledge_point = _parse_knowledge_markdown(source_file.read_text(encoding="utf-8"), source_id)
    ctx.checkpoint()
    out_path = ctx.artifacts.write_json(
        "knowledge_point.json",
        {
            "knowledge_point": knowledge_point,
            "source": {
                "file": source_file.name,
                "knowledge_dir": str(knowledge_dir),
            },
        },
    )
    log.info(
        "  wrote %s: %s（%s，%d 条易错点）",
        out_path.name,
        knowledge_point["title"],
        knowledge_point["grade"] or "未标注年级",
        len(knowledge_point["common_mistakes"]),
    )
