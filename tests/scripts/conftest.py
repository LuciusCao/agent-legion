import os
from pathlib import Path

import pytest

from scripts.check_architecture import check_repository

ROOT = Path(__file__).resolve().parents[2]

collect_ignore = (
    []
    if os.environ.get("AGENT_LEGION_DOCKER_TESTS") == "1"
    else [
        str(Path(__file__).with_name("test_install_worker_docker.py")),
        str(Path(__file__).with_name("test_worker_image_identity.py")),
    ]
)


@pytest.fixture(scope="session")
def real_repo_check_errors() -> tuple[str, ...]:
    # The full-repo scan is slow and its result cannot change mid-session;
    # run it once and share it. Returned as a tuple so consumers cannot
    # mutate the shared list.
    return tuple(check_repository(ROOT))
