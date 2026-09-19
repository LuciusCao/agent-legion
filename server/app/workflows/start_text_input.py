"""Start-node ``text_input`` block: how the dialog presents ``text`` items.

```yaml
_start:
  type: start
  accepted_item_types: [material, text]
  text_input:              # optional; every key optional
    label: 创作需求         # heading of the input box (default 需求内容)
    filename: 创作需求.md   # material filename for items without one
    template: |            # prefilled text the user edits before submitting
      # 歌曲创作需求
```

Presentation only: it never gates a submission (the contract is
``accepted_item_types``), so a definition may declare it without ``text``
accepted — the block is then inert, exactly like an unused config value.
Validation is shape-level (strings, bounded sizes, a bare ``.md``/``.txt``
filename) and mirrors the run-time rules in ``run_text_items``.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.schema import WorkflowDefinitionError, WorkflowTextInput

TEXT_INPUT_KEYS = ("label", "filename", "template")
MAX_LABEL_CHARS = 80
MAX_FILENAME_CHARS = 255
MAX_TEMPLATE_CHARS = 16 * 1024
_FILENAME_SUFFIXES = (".md", ".txt")


def load_text_input(raw: Any, node_key: str) -> WorkflowTextInput | None:
    """Parse ``text_input``; ``None``/absent → None (snapshots carry ``None``)."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or any(key not in TEXT_INPUT_KEYS for key in raw):
        raise WorkflowDefinitionError(
            f"Start node {node_key}.text_input must be a mapping with keys {list(TEXT_INPUT_KEYS)}"
        )
    values: dict[str, str] = {}
    for key, limit in (
        ("label", MAX_LABEL_CHARS),
        ("filename", MAX_FILENAME_CHARS),
        ("template", MAX_TEMPLATE_CHARS),
    ):
        value = raw.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str) or len(value) > limit:
            raise WorkflowDefinitionError(
                f"Start node {node_key}.text_input.{key} must be a string of at most {limit} chars"
            )
        # label/filename are trimmed; the template keeps its inner layout but
        # a whitespace-only template is no template (the dialog could never
        # tell "untouched" from "empty").
        values[key] = value.strip() if key != "template" else (value if value.strip() else "")
    filename = values["filename"]
    if filename and (
        "/" in filename
        or "\\" in filename
        or filename.startswith(".")
        or not filename.lower().endswith(_FILENAME_SUFFIXES)
    ):
        raise WorkflowDefinitionError(
            f"Start node {node_key}.text_input.filename must be a bare .md or .txt name"
        )
    text_input = WorkflowTextInput(**values)
    # An all-empty block is the same as no block: keeps echo/compare symmetric.
    return text_input if (text_input.label or filename or text_input.template) else None


def text_input_payload(text_input: WorkflowTextInput | None) -> dict[str, str] | None:
    """API/YAML echo of the block; None when undeclared so payloads stay clean."""
    if text_input is None:
        return None
    return {
        "label": text_input.label,
        "filename": text_input.filename,
        "template": text_input.template,
    }
