#!/usr/bin/env python3
"""Replace generic 'reading_analysis' references in tests with 'question_comprehension_info'."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests"


def should_process(path: Path) -> bool:
    if any(part in {".git", "__pycache__"} for part in path.parts):
        return False
    return path.suffix in {".py", ".json", ".yaml", ".yml", ".md"}


def main() -> None:
    for path in ROOT.rglob("*"):
        if not should_process(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new_text = text.replace("reading_analysis", "question_comprehension_info")
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"updated: {path.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
