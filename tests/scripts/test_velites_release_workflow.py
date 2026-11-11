"""velites Release 必须持续产出受支持 runner 上的三平台产物。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/velites-release.yml"


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_macos_build_uses_supported_runner_and_keeps_arm64_artifact() -> None:
    macos = _jobs()["build-macos"]

    assert macos["runs-on"] == "macos-15"
    assert macos["name"] == "build aarch64-apple-darwin"
    assert _step(macos, "Build release binary")["run"] == (
        "cargo build --release --locked --manifest-path velites/Cargo.toml"
    )
    assert _step(macos, "Smoke test")["run"] == "./velites/target/release/velites --version"
    assert "-aarch64-apple-darwin" in _step(macos, "Package")["run"]

    upload = next(
        step
        for step in macos["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"] == {
        "name": "velites-aarch64-apple-darwin",
        "path": "dist/*.tar.gz",
        "if-no-files-found": "error",
    }


def test_release_collects_all_three_platform_artifacts() -> None:
    jobs = _jobs()
    linux = jobs["build-linux"]

    assert {
        entry["triple"]: entry["runs-on"] for entry in linux["strategy"]["matrix"]["include"]
    } == {
        "x86_64-unknown-linux-gnu": "ubuntu-latest",
        "aarch64-unknown-linux-gnu": "ubuntu-24.04-arm",
    }
    assert linux["needs"] == jobs["build-macos"]["needs"] == "validate"

    release = jobs["release"]
    assert release["needs"] == ["build-linux", "build-macos"]
    assert _step(release, "Consolidate checksums")["run"] == (
        "cd dist && sha256sum *.tar.gz > sha256.txt"
    )
    publish = next(
        step
        for step in release["steps"]
        if str(step.get("uses", "")).startswith("softprops/action-gh-release@")
    )
    assert publish["with"]["files"].splitlines() == ["dist/*.tar.gz", "dist/sha256.txt"]
