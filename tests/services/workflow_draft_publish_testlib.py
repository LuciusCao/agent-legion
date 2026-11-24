from server.app.jobs.queries import JobQueries
from server.app.services.node_codes import NodeCodeService
from tests.postgres_support import TEST_DATABASE_URL

_DRAFT_YAML = """
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    capability: do_thing
"""


def _workspace(queries: JobQueries) -> dict:
    return queries.create_workspace("draft-publish-ws", workspace_id="test_publish_flow")


def _seed_node_code(workspace_id: str) -> None:
    """Publish a no-op workspace node code so the draft node is runnable."""
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(
        workspace_id,
        "test_publish_flow",
        "do_thing",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, "test_publish_flow", "do_thing")
