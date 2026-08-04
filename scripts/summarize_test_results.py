"""Render aggregate JUnit and pytest-rerun evidence as Markdown."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


@dataclass(frozen=True)
class JUnitSummary:
    path: Path
    tests: int
    failures: int
    errors: int
    skipped: int
    duration_seconds: float

    @property
    def passed(self) -> int:
        return max(0, self.tests - self.failures - self.errors - self.skipped)


def _float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def parse_junit(path: Path) -> JUnitSummary:
    root = ElementTree.parse(path).getroot()
    cases = root.findall(".//testcase")
    return JUnitSummary(
        path=path,
        tests=len(cases),
        failures=sum(case.find("failure") is not None for case in cases),
        errors=sum(case.find("error") is not None for case in cases),
        skipped=sum(case.find("skipped") is not None for case in cases),
        duration_seconds=sum(_float(case.get("time")) for case in cases),
    )


def parse_reruns(paths: list[Path]) -> tuple[int, int]:
    attempts = 0
    tests: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        attempts += int(payload.get("attempts", 0))
        tests.update(str(test) for test in payload.get("tests", []))
    return attempts, len(tests)


def _version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if output else "unavailable"


def render_summary(
    *,
    title: str,
    junit: list[JUnitSummary],
    rerun_attempts: int,
    rerun_test_count: int,
    missing: list[Path],
) -> str:
    lines = [f"## {title}", "", "### Execution context", ""]
    lines.extend(
        [
            "| Field | Value |",
            "| --- | --- |",
            f"| Commit | `{os.environ.get('GITHUB_SHA', 'local')}` |",
            f"| Platform | `{platform.system()} {platform.machine()}` |",
            f"| Logical CPUs | {os.cpu_count() or 'unknown'} |",
            f"| Python | `{platform.python_version()}` |",
            f"| Node | `{_version('node')}` |",
            f"| Pytest workers | `{os.environ.get('AGENT_LEGION_TEST_WORKERS', 'auto')}` |",
            f"| Coverage | `{os.environ.get('AGENT_LEGION_COV', 'frontend/default')}` |",
            "",
            "### Results",
            "",
        ]
    )
    if junit:
        lines.extend(
            [
                "| Report | Tests | Passed | Failed | Errors | Skipped | Case time |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for result in junit:
            lines.append(
                f"| `{result.path.name}` | {result.tests} | {result.passed} | "
                f"{result.failures} | {result.errors} | {result.skipped} | "
                f"{result.duration_seconds:.2f}s |"
            )
    else:
        lines.append("No JUnit reports were available.")
    lines.extend(
        [
            "",
            f"Rerun attempts: **{rerun_attempts}** across **{rerun_test_count}** tests.",
        ]
    )
    if missing:
        lines.extend(["", f"Missing optional evidence files: **{len(missing)}**."])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--junit", action="append", default=[], type=Path)
    parser.add_argument("--rerun-report", action="append", default=[], type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    missing = [path for path in [*args.junit, *args.rerun_report] if not path.exists()]
    junit: list[JUnitSummary] = []
    for path in args.junit:
        if not path.exists():
            continue
        try:
            junit.append(parse_junit(path))
        except (OSError, ElementTree.ParseError):
            missing.append(path)
    valid_rerun_reports: list[Path] = []
    for path in args.rerun_report:
        if not path.exists():
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            missing.append(path)
        else:
            valid_rerun_reports.append(path)
    rerun_attempts, rerun_test_count = parse_reruns(valid_rerun_reports)
    rendered = render_summary(
        title=args.title,
        junit=junit,
        rerun_attempts=rerun_attempts,
        rerun_test_count=rerun_test_count,
        missing=missing,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
