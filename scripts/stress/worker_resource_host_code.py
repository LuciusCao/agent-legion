"""Disposable Host-code sandbox support and its non-production evidence."""

from __future__ import annotations

from typing import Any

HOST_CODE_SANDBOX_OVERRIDE = """services:
  host:
    security_opt:
      - seccomp:unconfined
    cap_add:
      - SYS_ADMIN
      - NET_ADMIN
    command:
      - |
        apt-get update && apt-get install -y --no-install-recommends git bubblewrap && \
        chmod u+s /usr/bin/bwrap && \
        test -x /usr/bin/bwrap && test -u /usr/bin/bwrap && \
        chown -R "${MEASUREMENT_HOST_UID}:${MEASUREMENT_HOST_GID}" /var/lib/agent-legion && \
        exec setpriv --reuid="${MEASUREMENT_HOST_UID}" --regid="${MEASUREMENT_HOST_GID}" --clear-groups \
        env HOME=/tmp uvicorn server.app.main:create_prod_app --factory --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 20 --log-config deploy/uvicorn-log-config.json
"""


def host_code_sandbox_metadata(run: Any) -> dict[str, Any]:
    probe = run.compose(
        "exec",
        "-T",
        "host",
        "sh",
        "-c",
        "test -x /usr/bin/bwrap && test -u /usr/bin/bwrap && /usr/bin/bwrap --version && "
        "dpkg-query -W -f='${Package} ${Version}\\n' bubblewrap && "
        "stat -c '%a %A %U %G' /usr/bin/bwrap",
        capture=True,
    )
    return {
        "source": "Debian apt bubblewrap",
        "version_and_mode": probe,
        "path": "/usr/bin/bwrap",
        "setuid": True,
        "security_opt": "seccomp:unconfined",
        "cap_add": ["SYS_ADMIN", "NET_ADMIN"],
        "production_recommendation": False,
    }
