from typing import Any


def resume_workspace(session: Any, host_url: str, workspace_id: str) -> None:
    session.headers["x-agent-legion-request"] = "1"
    bootstrap = session.post(
        f"{host_url}/api/auth/bootstrap",
        json={"username": "native-spike-admin", "password": "native-spike-admin-pw"},
        timeout=5,
    )
    params = {"workspace_id": workspace_id}
    before = session.get(f"{host_url}/api/worker/status", params=params, timeout=5)
    resumed = session.post(f"{host_url}/api/worker/resume", params=params, timeout=5)
    after = session.get(f"{host_url}/api/worker/status", params=params, timeout=5)
    responses = (bootstrap, before, resumed, after)
    if any(response.status_code != 200 for response in responses) or (
        before.json(),
        after.json(),
    ) != ({"paused": True}, {"paused": False}):
        raise RuntimeError("native-spike startup pause/resume contract failed")
