#!/usr/bin/env python3
"""Spec health verification and classification script."""

import argparse
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REF_PATTERN = re.compile(
    r"(?<!http://)(?<!https://)(?<![\w/])"
    r"(?:server|frontend|config|tests)/[\w\-/]+\.(?:py|tsx|ts|yaml|yml|json|md)"
)

STATUS_PATTERN = re.compile(
    r"^\s*(?:\*\*)?(?:状态|Status)(?:\*\*)?\s*[:：]\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

ARCHIVE_BANNER = """> ⚠️ **此规格已归档**
>
> 该文档引用的部分代码路径已不存在或功能已变更，内容仅供参考，不作为当前系统的事实来源。
> 如需了解当前实现，请参阅 `docs/architecture/` 中的对应模块文档。
>
"""


def extract_refs(content: str) -> list[str]:
    """Extract code path references from markdown content."""
    refs = REF_PATTERN.findall(content)
    seen = set()
    unique_refs = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)
    return unique_refs


def parse_status(content: str) -> str:
    """Parse status field from spec content."""
    match = STATUS_PATTERN.search(content)
    if match:
        return match.group(1).strip().lower()
    return "已完成"


def check_refs(refs: list[str], root: Path) -> tuple[list[str], list[str]]:
    """Check which references exist in the repository.

    Returns (existing, missing).
    """
    existing = []
    missing = []
    for ref in refs:
        if (root / ref).exists():
            existing.append(ref)
        else:
            missing.append(ref)
    return existing, missing


def classify_spec(path: Path, root: Path) -> dict:
    """Classify a single spec file.

    Returns dict with keys: path, status, refs, existing, missing, target_dir
    """
    content = path.read_text(encoding="utf-8")
    status = parse_status(content)
    refs = extract_refs(content)
    existing, missing = check_refs(refs, root)

    if status in ("待批准", "进行中", "draft", "pending", "in_progress", "in progress"):
        target_dir = "specs"
    elif status in ("已完成", "completed", "done"):
        target_dir = "completed" if not missing else "archive"
    elif status in ("已废弃", "deprecated"):
        target_dir = "archive"
    else:
        target_dir = "completed" if not missing else "archive"

    return {
        "path": path,
        "status": status,
        "refs": refs,
        "existing": existing,
        "missing": missing,
        "target_dir": target_dir,
    }


def move_spec(src: Path, dst_dir: Path, dry_run: bool = False) -> Path | None:
    """Move a spec file to target directory using git mv."""
    dst = dst_dir / src.name
    if src.resolve() == dst.resolve():
        return None

    if dry_run:
        print(f"[DRY-RUN] Would move {src} -> {dst}")
        return dst

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "mv", str(src), str(dst)],
            check=True,
            capture_output=True,
            text=True,
            cwd=str(src.parent),
        )
    except subprocess.CalledProcessError:
        shutil.move(str(src), str(dst))
    return dst


def inject_banner(path: Path, dry_run: bool = False) -> bool:
    """Inject archive banner into spec file if not already present.

    Returns True if banner was (or would be) injected.
    """
    content = path.read_text(encoding="utf-8")
    if ARCHIVE_BANNER.strip() in content:
        return False

    if dry_run:
        print(f"[DRY-RUN] Would inject banner into {path}")
        return True

    lines = content.splitlines()
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_idx = i
            break

    new_lines = lines[:insert_idx] + ["", ARCHIVE_BANNER.rstrip()] + lines[insert_idx:]
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


def generate_report(classifications: list[dict], output_path: Path) -> None:
    """Generate SPEC_HEALTH.md report."""
    lines = [
        "# Spec Health Report",
        "",
        f"自动生成于：{datetime.now().strftime('%Y-%m-%d')}",
        "",
        "## 汇总",
        "",
        "| 类别 | 数量 | 说明 |",
        "|------|------|------|",
    ]

    counts = {"specs": 0, "completed": 0, "archive": 0}
    for c in classifications:
        counts[c["target_dir"]] = counts.get(c["target_dir"], 0) + 1

    lines.append(f"| specs/（进行中） | {counts['specs']} | 待批准或进行中的设计规格 |")
    lines.append(f"| completed/（已完成） | {counts['completed']} | 已完成且代码引用有效的规格 |")
    lines.append(f"| archive/（已归档） | {counts['archive']} | 引用已失效或已废弃的规格 |")

    lines.extend(
        [
            "",
            "## 明细",
            "",
            "| Spec | Status | Missing Refs | Location |",
            "|------|--------|--------------|----------|",
        ]
    )

    for c in sorted(classifications, key=lambda x: x["path"].name):
        status_label = "healthy" if not c["missing"] else "stale"
        missing_count = len(c["missing"])
        lines.append(
            f"| {c['path'].name} | {status_label} | {missing_count} | {c['target_dir']}/ |"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_check(classifications: list[dict]) -> int:
    """Run in check mode for CI integration.

    Returns 0 if all active specs are healthy, 1 otherwise.
    """
    errors = []
    for c in classifications:
        if c["target_dir"] == "specs" and c["missing"]:
            errors.append(
                f"ERROR: Active spec {c['path'].name} has {len(c['missing'])} missing refs"
            )
        elif c["target_dir"] == "completed" and c["missing"]:
            errors.append(
                f"ERROR: Completed spec {c['path'].name} has {len(c['missing'])} missing refs (run verify_specs.py to reclassify)"
            )

    for e in errors:
        print(e, file=sys.stderr)

    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and classify design specs")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root")
    parser.add_argument("--dry-run", action="store_true", help="Show without changes")
    parser.add_argument("--check", action="store_true", help="CI check mode")
    parser.add_argument("--report-only", action="store_true", help="Only generate report")
    args = parser.parse_args()

    root = args.root.resolve()
    specs_dir = root / "docs" / "superpowers" / "specs"

    if args.report_only:
        all_specs = []
        for subdir_name in ("specs", "completed", "archive"):
            subdir = root / "docs" / "superpowers" / subdir_name
            if subdir.exists():
                all_specs.extend(subdir.glob("*.md"))
        classifications = [classify_spec(p, root) for p in all_specs]
        generate_report(classifications, root / "docs" / "superpowers" / "SPEC_HEALTH.md")
        return 0

    spec_files = list(specs_dir.glob("*.md"))
    classifications = [classify_spec(p, root) for p in spec_files]

    if args.check:
        return run_check(classifications)

    for c in classifications:
        target = root / "docs" / "superpowers" / c["target_dir"]
        new_path = move_spec(c["path"], target, args.dry_run)
        if new_path:
            c["path"] = new_path

    for c in classifications:
        if c["target_dir"] == "archive":
            inject_banner(c["path"], args.dry_run)

    if not args.dry_run:
        generate_report(classifications, root / "docs" / "superpowers" / "SPEC_HEALTH.md")

    return 0


if __name__ == "__main__":
    sys.exit(main())
