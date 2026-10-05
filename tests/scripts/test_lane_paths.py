"""Changed-path lane classification pins (#941, #917 T-1/T-2).

One path table drives all three lane-trimming entry points — the CI
``changes`` job (its real ``run:`` script extracted from quality-gate.yml),
the local quick gate's worktree derivation and the pre-push hook — so they
cannot drift apart on what counts as docs. Runtime markdown (the Studio
bootstrap prompt, MCP guides, example skills) must run its directory's lane,
and ``velites/schema/**`` must also run the backend lane.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.no_db

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZERO_SHA = "0" * 40

# path -> (CI lanes that turn on, local lanes string)
# CI maps an ordinary non-frontend path to backend+frontend; the local gates
# map it to backend only. Docs are "no CI lane" and "static" locally.
CASES: list[tuple[str, set[str], str]] = [
    ("README.md", set(), "static"),
    ("CHANGELOG.md", set(), "static"),
    ("docs/architecture/backend.md", set(), "static"),
    ("LICENSE", set(), "static"),
    ("server/app/studio_chat/authoring_bootstrap.md", {"backend", "frontend"}, "backend"),
    ("mcp_server/authoring_guide.md", {"backend", "frontend"}, "backend"),
    ("mcp_server/preview_guide.md", {"backend", "frontend"}, "backend"),
    ("examples/skills/demo/writer/SKILL.md", {"backend", "frontend"}, "backend"),
    ("frontend/README.md", {"frontend"}, "frontend"),
    ("velites/README.md", {"rust"}, "rust"),
    ("velites/src/main.rs", {"rust"}, "rust"),
    ("velites/schema/events.schema.json", {"backend", "rust"}, "backend rust"),
]
PATH_IDS = [case[0] for case in CASES]


def _git(args: list[str], cwd: Path, **kwargs: object) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="Lane Test",
        GIT_AUTHOR_EMAIL="lane@example.com",
        GIT_COMMITTER_NAME="Lane Test",
        GIT_COMMITTER_EMAIL="lane@example.com",
    )
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=True, **kwargs
    )


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _touch(repo: Path, rel: str, content: str | None = None) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if content is not None else f"change {rel}\n", encoding="utf-8")


def _init_repo(repo: Path) -> str:
    _git(["init", "-q"], cwd=repo)
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-qm", "fixture"], cwd=repo)
    return _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()


def _commit_path(repo: Path, rel: str, content: str | None = None) -> None:
    _touch(repo, rel, content)
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-qm", f"touch {rel}"], cwd=repo)


def _seed_rename_source(repo: Path, rename_from: str | None) -> None:
    if rename_from is not None:
        _touch(repo, rename_from, '{"title": "fixture schema"}\n')


def _change(repo: Path, rel: str, rename_from: str | None, *, commit: bool) -> None:
    """Touch ``rel``, or ``git mv`` an existing ``rename_from`` onto it."""
    if rename_from is None:
        if commit:
            _commit_path(repo, rel)
        else:
            _touch(repo, rel)
        return
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    _git(["mv", rename_from, rel], cwd=repo)
    if commit:
        _git(["commit", "-qm", f"rename {rename_from}"], cwd=repo)


def _ci_filter_script() -> str:
    workflow = yaml.safe_load(
        (PROJECT_ROOT / ".github/workflows/quality-gate.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["changes"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "filter")
    assert "${{" not in script, "filter script must read inputs from env only"
    return script


def _ci_lanes(tmp_path: Path, rel: str, rename_from: str | None = None) -> set[str]:
    repo = tmp_path / "ci"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "lane-paths.sh", repo / "scripts" / "lane-paths.sh")
    _seed_rename_source(repo, rename_from)
    base = _init_repo(repo)
    _change(repo, rel, rename_from, commit=True)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(EVENT_NAME="pull_request", PR_BASE=base, GITHUB_OUTPUT=str(output))
    subprocess.run(
        ["bash", "-e", "-c", _ci_filter_script()],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    flags = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    return {lane for lane in ("backend", "frontend", "rust") if flags[lane] == "true"}


def _quick_gate_lanes(
    tmp_path: Path, rel: str, content: str | None = None, rename_from: str | None = None
) -> str:
    repo = tmp_path / "quick"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in ("check-quick.sh", "gate-jobs.sh", "gate-queue.sh", "lane-paths.sh"):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, scripts / name)
    for name in ("check-quick-backend.sh", "check-quick-frontend.sh"):
        _write_executable(scripts / name, "#!/usr/bin/env bash\nexit 0\n")
    _seed_rename_source(repo, rename_from)
    _init_repo(repo)
    if rename_from is None:
        _touch(repo, rel, content)
    else:
        _change(repo, rel, rename_from, commit=False)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") and k not in {"GATE_LANES", "GATE_TIER"}
    }
    result = subprocess.run(
        [str(scripts / "check-quick.sh")],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    prefix = "Derived lanes from worktree changes: "
    derived = [line for line in result.stdout.splitlines() if line.startswith(prefix)]
    assert derived, result.stdout + result.stderr
    return derived[0].removeprefix(prefix)


def _pre_push_lanes(
    tmp_path: Path, rel: str, content: str | None = None, rename_from: str | None = None
) -> str:
    repo = tmp_path / "hook"
    (repo / ".githooks").mkdir(parents=True)
    (repo / "scripts").mkdir()
    shutil.copy2(PROJECT_ROOT / ".githooks" / "pre-push", repo / ".githooks" / "pre-push")
    for name in ("run-local-gate.sh", "lane-paths.sh"):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, repo / "scripts" / name)
    gate_log = tmp_path / "gate.log"
    _write_executable(
        repo / "scripts" / "check-quick.sh",
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "${GATE_LANES:-}" >>"$GATE_LOG"\n',
    )
    _seed_rename_source(repo, rename_from)
    base = _init_repo(repo)
    if rename_from is None:
        _commit_path(repo, rel, content)
    else:
        _change(repo, rel, rename_from, commit=True)
    head = _git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GATE_LOG"] = str(gate_log)
    env.pop("AGENT_LEGION_GATE_LEVEL", None)
    subprocess.run(
        [str(repo / ".githooks" / "pre-push")],
        cwd=repo,
        env=env,
        input=f"refs/heads/local {head} refs/heads/feature/lanes {base}\n",
        text=True,
        capture_output=True,
        check=True,
    )
    return gate_log.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize(("rel", "ci_expected", "local_expected"), CASES, ids=PATH_IDS)
def test_ci_changes_job_classifies_paths(
    tmp_path: Path, rel: str, ci_expected: set[str], local_expected: str
) -> None:
    assert _ci_lanes(tmp_path, rel) == ci_expected


@pytest.mark.parametrize(("rel", "ci_expected", "local_expected"), CASES, ids=PATH_IDS)
def test_quick_gate_classifies_paths(
    tmp_path: Path, rel: str, ci_expected: set[str], local_expected: str
) -> None:
    assert _quick_gate_lanes(tmp_path, rel) == local_expected


@pytest.mark.parametrize(("rel", "ci_expected", "local_expected"), CASES, ids=PATH_IDS)
def test_pre_push_classifies_paths(
    tmp_path: Path, rel: str, ci_expected: set[str], local_expected: str
) -> None:
    assert _pre_push_lanes(tmp_path, rel) == local_expected


def test_ci_changes_job_runs_every_lane_when_classifier_changes(tmp_path: Path) -> None:
    # A broken classifier must not be able to skip the lanes that test it:
    # the CI filter refuses to source rules the diff itself modified.
    repo = tmp_path / "ci"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "lane-paths.sh", repo / "scripts" / "lane-paths.sh")
    base = _init_repo(repo)
    (repo / "scripts" / "lane-paths.sh").write_text(
        "lane_path_is_docs() { return 0; }\nlane_path_feeds_backend() { return 1; }\n",
        encoding="utf-8",
    )
    _git(["commit", "-qam", "break classifier"], cwd=repo)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(EVENT_NAME="pull_request", PR_BASE=base, GITHUB_OUTPUT=str(output))
    subprocess.run(
        ["bash", "-e", "-c", _ci_filter_script()],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    flags = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert flags == {"backend": "true", "frontend": "true", "rust": "true", "docker": "true"}


def test_ci_classifier_guard_survives_large_diffs(tmp_path: Path) -> None:
    # Many paths after the classifier in diff order must not let an
    # early-exiting matcher SIGPIPE the producer and read as "unchanged".
    repo = tmp_path / "ci"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "lane-paths.sh", repo / "scripts" / "lane-paths.sh")
    base = _init_repo(repo)
    (repo / "scripts" / "lane-paths.sh").write_text(BROKEN_CLASSIFIER, encoding="utf-8")
    bulk = repo / "zz"
    bulk.mkdir()
    for i in range(20000):
        (bulk / f"f{i:05d}.md").write_text("x\n", encoding="utf-8")
    _git(["add", "-A"], cwd=repo)
    _git(["commit", "-qm", "break classifier + bulk"], cwd=repo)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(EVENT_NAME="pull_request", PR_BASE=base, GITHUB_OUTPUT=str(output))
    subprocess.run(
        ["bash", "-e", "-c", _ci_filter_script()],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    flags = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert set(flags.values()) == {"true"}


def test_ci_changes_job_keeps_lanes_off_for_empty_diff(tmp_path: Path) -> None:
    repo = tmp_path / "ci"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "scripts" / "lane-paths.sh", repo / "scripts" / "lane-paths.sh")
    base = _init_repo(repo)
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(EVENT_NAME="pull_request", PR_BASE=base, GITHUB_OUTPUT=str(output))
    subprocess.run(
        ["bash", "-e", "-c", _ci_filter_script()],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    flags = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert set(flags.values()) == {"false"}


# git mv of a backend-read schema into docs/: rename detection would report
# only the docs/ target; the source path must still drive backend + rust.
RENAME = ("velites/schema/events.schema.json", "docs/events.schema.json")


def test_ci_changes_job_classifies_rename_source(tmp_path: Path) -> None:
    source, target = RENAME
    assert _ci_lanes(tmp_path, target, rename_from=source) == {"backend", "rust"}


def test_quick_gate_classifies_rename_source(tmp_path: Path) -> None:
    source, target = RENAME
    assert _quick_gate_lanes(tmp_path, target, rename_from=source) == "backend rust"


def test_pre_push_classifies_rename_source(tmp_path: Path) -> None:
    source, target = RENAME
    assert _pre_push_lanes(tmp_path, target, rename_from=source) == "backend rust"


BROKEN_CLASSIFIER = "lane_path_is_docs() { return 0; }\nlane_path_feeds_backend() { return 1; }\n"


def test_quick_gate_runs_every_lane_when_classifier_changes(tmp_path: Path) -> None:
    # Same control-plane rule locally: an edited (here: everything-is-docs)
    # classifier is never consulted for its own change set.
    assert _quick_gate_lanes(tmp_path, "scripts/lane-paths.sh", BROKEN_CLASSIFIER) == (
        "backend frontend rust"
    )


def test_pre_push_runs_every_lane_when_classifier_changes(tmp_path: Path) -> None:
    assert _pre_push_lanes(tmp_path, "scripts/lane-paths.sh", BROKEN_CLASSIFIER) == (
        "backend frontend rust"
    )


def test_case_table_agrees_on_docs_between_ci_and_local() -> None:
    # The table itself encodes the cross-entry-point contract: a path is docs
    # for CI (no lane) exactly when it is docs locally (static phase), and a
    # backend-feeding path turns on backend on both sides.
    for rel, ci_expected, local_expected in CASES:
        assert (not ci_expected) == (local_expected == "static"), rel
        assert ("backend" in ci_expected) == ("backend" in local_expected.split()), rel
