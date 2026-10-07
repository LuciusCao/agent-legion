"""Published probe preview panel for the strict-CSP browser smoke (#989).

The panel is published on the demo workspace before the backend boots
(seed-before-serve, PR #240). It exercises the two halves of the strict
document policy (server/app/http_csp.py) as frontend/e2e/smoke-preview-csp.spec.ts
asserts them: its ``<script>`` must run (the host stamps the per-response
nonce) and its inline ``onclick=`` attribute must stay blocked, surfacing the
host's interception banner.
"""

from __future__ import annotations

from pathlib import Path

from server.app.jobs import JobQueries
from server.app.services.preview_panels import PreviewPanelService

PROBE_PANEL_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>csp probe</title></head>
<body>
<div id="status">script-blocked</div>
<div id="init">no-init</div>
<button id="inline-handler" onclick="document.getElementById('status').textContent='inline-handler-ran'">inline</button>
<button id="listener">listener</button>
<script>
document.getElementById('status').textContent = 'script-ran'
document.getElementById('listener').addEventListener('click', function () {
  document.getElementById('status').textContent = 'listener-ran'
})
window.addEventListener('message', function (event) {
  var data = event.data
  if (data && data.source === 'agent-legion-preview-host' && data.type === 'init') {
    document.getElementById('init').textContent = 'init:' + data.jobId
  }
})
window.parent.postMessage({ source: 'agent-legion-preview-panel', type: 'ready' }, '*')
</script>
</body></html>
"""


def seed_probe_preview_panel(dsn: str, data_dir: Path, workspace_id: str) -> None:
    """Save and publish the probe bundle as ``workspace_id``'s preview panel."""
    service = PreviewPanelService(JobQueries(dsn, jobs_dir=data_dir / "jobs"))
    draft = service.save_draft(workspace_id, PROBE_PANEL_HTML, "e2e-seed", "#989 strict CSP probe")
    service.publish(workspace_id, draft["html_hash"])
