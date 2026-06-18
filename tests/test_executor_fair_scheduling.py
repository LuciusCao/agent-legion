from server.app.executors.scheduling.fair import WorkspaceRoundRobin


def test_round_robin_claims_at_most_one_per_workspace_per_round():
    scheduler = WorkspaceRoundRobin()
    assert scheduler.order(["a", "a", "b", "c", "c"]) == ["a", "b", "c"]


def test_round_robin_rotates_starting_workspace():
    scheduler = WorkspaceRoundRobin()
    assert scheduler.order(["a", "b", "c"]) == ["a", "b", "c"]
    scheduler.complete_pass("a")
    assert scheduler.order(["a", "b", "c"]) == ["b", "c", "a"]


def test_idle_workspace_does_not_reserve_capacity():
    scheduler = WorkspaceRoundRobin()
    assert scheduler.order(["busy", "busy"]) == ["busy"]
