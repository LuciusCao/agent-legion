#!/usr/bin/env python3
"""write-script 输出校验：script.md 存在、结构齐全、长度达标。

用法：python validate_output.py <job_dir>；退出码 0 = 通过。
只依赖标准库。
"""

from __future__ import annotations

import sys
from pathlib import Path

REQUIRED_SECTIONS = ("## 开场导入", "## 概念讲解", "## 例题演示", "## 易错点提醒", "## 小结")
MIN_BODY_CHARS = 200


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_output.py <job_dir>", file=sys.stderr)
        return 2
    job_dir = Path(sys.argv[1])
    script_path = job_dir / "script.md"
    if not script_path.is_file():
        print("missing required output: script.md", file=sys.stderr)
        return 1
    try:
        text = script_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"script.md is not valid UTF-8: {exc}", file=sys.stderr)
        return 1
    missing = [section for section in REQUIRED_SECTIONS if section not in text]
    if missing:
        print(f"script.md missing sections: {', '.join(missing)}", file=sys.stderr)
        return 1
    body_chars = len("".join(text.split()))
    if body_chars < MIN_BODY_CHARS:
        print(
            f"script.md too short: {body_chars} chars (minimum {MIN_BODY_CHARS})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
