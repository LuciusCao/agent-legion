from server.app.services.job_staged_cleanup import commit_staged_outputs


class _Staged:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1
        if self._fail:
            raise RuntimeError("cleanup failed")


def test_commit_staged_outputs_noop_when_none():
    commit_staged_outputs(None, "job-1", "rerun")


def test_commit_staged_outputs_commits_staged():
    staged = _Staged()
    commit_staged_outputs(staged, "job-1", "rerun")
    assert staged.commits == 1


def test_commit_staged_outputs_swallows_commit_failure():
    staged = _Staged(fail=True)
    commit_staged_outputs(staged, "job-1", "rerun")
    assert staged.commits == 1
