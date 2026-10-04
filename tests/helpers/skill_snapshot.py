"""Small real Git repositories for authoring contract tests."""

import subprocess


def git(repo, *args):
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        check=True,
        capture_output=True,
    ).stdout


def commit(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture", "--no-gpg-sign")
