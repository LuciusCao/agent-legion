"""In-process CMS stub and external-connection seeding for the browser smoke.

Deterministic and offline: question-detail lookups are served by a stub HTTP
server, and the ``cms-internal`` instance-level external connection (schema
v34) is seeded to point at it with a dummy static token (the stub ignores
auth). Extracted from run_browser_smoke.py for the file-size budget.
"""

from __future__ import annotations

import http.server
import json
import logging
import threading
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def start_cms_stub(port: int) -> http.server.ThreadingHTTPServer:
    """Serve deterministic CMS question-detail responses on 127.0.0.1."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib API)
            uuid = parse_qs(urlparse(self.path).query).get("uuid", [""])[0]
            payload = {
                "code": 0,
                "message": "success",
                "data": {
                    "question_uuid": uuid,
                    "question_title": f"E2E 题目 {uuid}",
                    "body": {"content": f"E2E stub stem for {uuid}"},
                },
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("cms-stub: " + format, *args)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def seed_cms_connection(db_dsn: str, cms_base_url: str, vault_key: str) -> None:
    """Point the cms-internal external connection at the in-process stub.

    The env CMS_* channel is retired (schema v34): runtime CMS access goes
    through the instance-level connection, so the smoke seeds one directly —
    a static_bearer connection whose token is a dummy vault entry and whose
    base_url is the stub.
    """
    from cryptography.fernet import Fernet

    from server.app.db.transaction import write_transaction

    config = {
        "base_url": cms_base_url,
        "probe_url": f"{cms_base_url}/question/detail",
        "token": {"secret_ref": "conn:cms-internal:token"},
    }
    ciphertext = Fernet(vault_key.encode("utf-8")).encrypt(b"e2e-stub-token").decode("utf-8")
    with write_transaction(db_dsn) as conn:
        conn.execute(
            "insert into external_connections(key, type, display_name, config_json)"
            " values ('cms-internal', 'static_bearer', 'E2E CMS stub', %s)"
            " on conflict(key) do nothing",
            (json.dumps(config, ensure_ascii=False),),
        )
        conn.execute(
            "insert into instance_secrets(name, ciphertext)"
            " values ('conn:cms-internal:token', %s) on conflict(name) do nothing",
            (ciphertext,),
        )
