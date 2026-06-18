import asyncio
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn

from server.app.main import create_app


@contextmanager
def _run_test_server(data_dir):
    """Start the app on a free localhost port and yield its base URL."""
    app = create_app(data_dir=data_dir, start_worker=False)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            access_log=False,
            loop="asyncio",
        )
    )

    def _serve():
        asyncio.run(server.serve())

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        port = None
        for _ in range(100):
            servers = getattr(server, "servers", None)
            if servers:
                port = servers[0].sockets[0].getsockname()[1]
                break
            time.sleep(0.05)
        if port is None:
            raise RuntimeError("Uvicorn server did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.full_gate
def test_workspace_sse_receives_jobs_created(tmp_path):
    # Note: TestClient buffers infinite streaming responses and never yields
    # live SSE chunks, so we run a real Uvicorn server for this test.
    with _run_test_server(tmp_path) as base_url:
        client = httpx.Client(trust_env=False)
        try:
            # Create workspace first via API
            resp = client.post(f"{base_url}/api/workspaces", json={"name": "sse-test"})
            assert resp.status_code == 200
            workspace_id = resp.json()["workspace"]["id"]

            with client.stream(
                "GET",
                f"{base_url}/api/workspaces/{workspace_id}/events",
                timeout=httpx.Timeout(10.0, read=30.0),
            ) as stream:
                # Create a job batch to trigger an event
                resp = client.post(
                    f"{base_url}/api/workspaces/{workspace_id}/job-batches",
                    json={
                        "workflow_key": "question_content",
                        "source_kind": "direct_ids",
                        "question_ids": ["q123"],
                        "knowledge_codes": [],
                    },
                )
                assert resp.status_code == 200

                # Read until we see our event or timeout
                found = False
                for chunk in stream.iter_text():
                    if '"type": "jobs_created"' in chunk:
                        found = True
                        break
                assert found
        finally:
            client.close()
