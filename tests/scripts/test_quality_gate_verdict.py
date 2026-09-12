"""Executable contract tests for the final GitHub Actions gate verdict."""

from __future__ import annotations

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/quality-gate.yml"
LANES = (
    "changes",
    "backend-unit",
    "api-check",
    "backend-postgres",
    "docs-terms",
    "backend-coverage",
    "frontend-logic",
    "frontend-component",
    "frontend-coverage",
    "e2e-smoke",
    "rust",
    "docker-build",
)


def _aggregate_command() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["quality-gate"]["steps"][0]["run"]


def _passing_context(
    *, backend: bool = False, frontend: bool = False, rust: bool = False, docker: bool = False
) -> dict[str, dict[str, object]]:
    context: dict[str, dict[str, object]] = {
        lane: {"result": "skipped", "outputs": {}} for lane in LANES
    }
    context["changes"] = {
        "result": "success",
        "outputs": {
            "backend": str(backend).lower(),
            "frontend": str(frontend).lower(),
            "rust": str(rust).lower(),
            "docker": str(docker).lower(),
        },
    }
    if not backend:
        context["docs-terms"]["result"] = "success"
    if backend:
        for lane in ("backend-unit", "backend-postgres", "backend-coverage"):
            context[lane]["result"] = "success"
    if backend or frontend:
        context["api-check"]["result"] = "success"
    if frontend:
        for lane in ("frontend-logic", "frontend-component", "frontend-coverage"):
            context[lane]["result"] = "success"
    if backend or frontend or rust:
        context["e2e-smoke"]["result"] = "success"
    if rust:
        context["rust"]["result"] = "success"
    if docker:
        context["docker-build"]["result"] = "success"
    return context


def _verdict(context: dict[str, dict[str, object]]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["NEEDS_CONTEXT"] = json.dumps(context)
    return subprocess.run(
        ["bash", "-c", _aggregate_command()],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


@pytest.mark.parametrize(
    "context",
    [
        _passing_context(),
        _passing_context(backend=True),
        _passing_context(frontend=True),
        _passing_context(rust=True),
        _passing_context(backend=True, frontend=True, rust=True, docker=True),
    ],
    ids=("docs-only", "backend", "frontend", "rust", "full"),
)
def test_selected_lanes_pass_only_when_their_jobs_succeed(
    context: dict[str, dict[str, object]],
) -> None:
    result = _verdict(context)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("base", "lane"),
    [
        (_passing_context(), "docs-terms"),
        (_passing_context(backend=True), "backend-unit"),
        (_passing_context(backend=True), "backend-postgres"),
        (_passing_context(frontend=True), "api-check"),
        (_passing_context(frontend=True), "frontend-component"),
        (_passing_context(rust=True), "e2e-smoke"),
        (_passing_context(rust=True), "rust"),
        (_passing_context(docker=True), "docker-build"),
    ],
)
def test_selected_lane_cannot_silently_skip(base: dict[str, dict[str, object]], lane: str) -> None:
    context = deepcopy(base)
    context[lane]["result"] = "skipped"
    assert _verdict(context).returncode != 0


def test_changes_failure_always_fails_the_aggregate() -> None:
    context = _passing_context(backend=True, frontend=True, rust=True, docker=True)
    context["changes"]["result"] = "failure"
    assert _verdict(context).returncode != 0
