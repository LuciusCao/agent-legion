"""Full-state PUT payload validation for the ``_shared`` materials (#633).

Every rule runs before any disk write (the route's staged swap then either
applies the whole payload or nothing). Extracted from the route module when
the codex R3 hardening (UTF-8 byte cap, mapped-source completeness) pushed
its inline validation past the route's file budget — the service keeps the
validation story in one testable place, the route stays the HTTP seam.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from server.app.services.skill_repo import MAX_FILE_BYTES
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_sync import MAP_PATH, validate_materials

_MATERIAL_DIRS = ("references", "scripts")


def validate_shared_put_payload(shared_dir: Path, files: list[tuple[str, str]]) -> dict[str, str]:
    """Validate one full-state payload → the ``{relative path: content}``
    map the staged swap writes. Raises ``SkillEditValidationError`` on the
    FIRST failed rule class (paths, byte caps, map shape, source
    completeness) with the structured error list the route renders as 422.

    Rules (in order):
    - paths: map.json at the root, everything else under references/ or
      scripts/, no ``..``/absolute/``.git`` components, no duplicates
      (codex R2 P1), no symlink escape after resolution;
    - byte cap (codex R3 P1): content is measured in raw UTF-8 BYTES —
      the wire contract counts characters, a CJK/emoji-heavy file can
      pass it yet exceed the 128 KiB disk cap, and the sync would
      silently truncate (or corrupt a multi-byte sequence) into every
      mapped skill;
    - map.json present, parseable, ``version == 1``, materials schema
      (``validate_materials``);
    - source completeness (codex R3 P2): the PUT replaces the whole
      ``_shared`` directory, so every mapped ``source`` must arrive in
      the same payload — a missing one would strand every mapped
      ``save_skill_version`` on "shared source unreadable".
    """
    root = shared_dir.resolve()
    errors: list[dict[str, str]] = []
    targets: dict[str, str] = {}
    for raw_path, content in files:
        parts = PurePosixPath(raw_path).parts
        if (
            not raw_path
            or PurePosixPath(raw_path).is_absolute()
            or ".." in parts
            or any(part.lower() == ".git" for part in parts)
            or (raw_path != MAP_PATH and (len(parts) < 2 or parts[0] not in _MATERIAL_DIRS))
        ):
            errors.append(
                {
                    "path": raw_path or ".",
                    "error": "path must be map.json at the root or stay under "
                    "references/ or scripts/, with no '..'/absolute/.git components",
                }
            )
            continue
        resolved = (root / raw_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            errors.append({"path": raw_path, "error": "path escapes the _shared directory"})
            continue
        relative = resolved.relative_to(root).as_posix()
        if relative in targets:
            errors.append(
                {"path": relative, "error": "duplicate path in payload (map to ONE file)"}
            )
            continue
        byte_size = len(content.encode("utf-8"))
        if byte_size > MAX_FILE_BYTES:
            errors.append(
                {
                    "path": relative,
                    "error": f"content is {byte_size} UTF-8 bytes; the maximum "
                    f"shared-material size is {MAX_FILE_BYTES} bytes",
                }
            )
            continue
        targets[relative] = content
    if errors:
        raise SkillEditValidationError("Invalid shared material paths", errors)

    map_content = targets.get(MAP_PATH)
    if map_content is None:
        raise SkillEditValidationError(
            "Invalid shared materials map",
            [{"path": MAP_PATH, "error": "map.json is required in the payload"}],
        )
    try:
        parsed = json.loads(map_content)
    except json.JSONDecodeError as exc:
        raise SkillEditValidationError(
            "Invalid shared materials map",
            [{"path": MAP_PATH, "error": f"malformed JSON: {exc}"}],
        ) from exc
    if not isinstance(parsed, dict) or parsed.get("version") != 1:
        raise SkillEditValidationError(
            "Invalid shared materials map",
            [{"path": MAP_PATH, "error": "version must be 1"}],
        )
    materials = validate_materials(parsed.get("materials"))
    material_paths = {path for path in targets if path != MAP_PATH}
    missing = sorted(m.source for m in materials if m.source not in material_paths)
    if missing:
        raise SkillEditValidationError(
            "Invalid shared materials map",
            [
                {
                    "path": MAP_PATH,
                    "error": "every mapped source must be present in the same "
                    "full-state payload (the PUT replaces the whole _shared "
                    f"directory); missing: {', '.join(missing)}",
                }
            ],
        )
    return targets
