"""Demo workflow intake node: parse one knowledge-point material into job input.

First node of the ``education_video_problems_generation`` example workflow
(capability ``intake_knowledge_points``).

How job input reaches this node (materials-and-runs design §4/§6.2):

- The job's input is a **material item**: a knowledge-point markdown the user
  uploaded to the workspace (``jobs.input_json`` =
  ``{"type": "material", "material_id": ...}``). The demo workspace is seeded
  with the repo's ``examples/`` markdown as ready-to-use sample materials
  (``server/app/services/demo_material_seed.py``, seed-if-absent).
- The dispatching parent has already materialized the object into the local
  content-addressed cache, so the node simply reads
  ``ctx.material["path"]`` — a local read-only file inside the sandbox's
  static allow-read cache root (MATERIAL-ACCESS-001). No ``knowledge_dir``
  configuration, no repo ``examples/`` access: the node works identically on
  the Host and on a remote Worker.
- The node parses the markdown and writes ``knowledge_point.json`` for the
  downstream agent nodes; the material file stem doubles as the knowledge
  point id.

This file is the git-reviewed **seed source** of the demo intake node: when
a workspace binds the demo workflow it is published as a workspace node_code
version (EXEC-CODE-002, #96) and executes from the DB text inside the
velites sandbox. Pure stdlib + node SDK: no business imports, no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace_libs.node_sdk import NodeContext, entrypoint

_HEADING_1 = "# "
_HEADING_2 = "## "
_META_BULLET = "- "
_CONCEPT_SECTION = "核心概念"
_MISTAKES_SECTION = "常见易错点"


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

    material = ctx.material
    if material is None:
        raise RuntimeError(
            "example_intake 需要 material 类型的 job 输入：请上传知识点 markdown"
            "材料（demo workspace 已预置示例材料）后创建运行；当前 job 没有材料输入"
        )
    source_file = Path(str(material["path"]))
    source_id = source_file.stem or str(ctx.job.get("source_id") or "")
    log.info(
        "example_intake: material_id=%s file=%s",
        material.get("material_id", ""),
        source_file.name,
    )

    knowledge_point = _parse_knowledge_markdown(source_file.read_text(encoding="utf-8"), source_id)
    ctx.checkpoint()
    out_path = ctx.artifacts.write_json(
        "knowledge_point.json",
        {
            "knowledge_point": knowledge_point,
            "source": {
                "file": source_file.name,
                "material_id": str(material.get("material_id") or ""),
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
