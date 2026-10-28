"""Disposable Host-code sandbox support and its non-production evidence."""

from __future__ import annotations

from typing import Any

_READ_LOG = (
    "import functools,os,stat,sys; "
    "fd=os.open(sys.argv[2],os.O_RDONLY|os.O_DIRECTORY); parts=sys.argv[1].split('/'); "
    "assert parts[:3]==['logs','jobs','by-job'] and len(parts)==6; "
    "fd=functools.reduce(lambda current,part: (os.open(part,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY,dir_fd=current), "
    "os.close(current))[0], parts[:-1], fd); "
    "leaf=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd); os.close(fd); "
    "assert stat.S_ISREG(os.fstat(leaf).st_mode); "
    "sys.stdout.buffer.write(os.read(leaf,65536)); os.close(leaf)"
)


def _bounded(text: object, limit: int) -> str:
    return str(text).encode("utf-8")[:limit].decode("utf-8", "ignore")


HOST_CODE_SANDBOX_OVERRIDE = """services:
  host:
    security_opt:
      - seccomp:unconfined
      # Measurement-only: Ubuntu's default AppArmor profile can deny bwrap capset.
      - apparmor:unconfined
    # Measurement-only: non-root setuid bwrap restores REQUIRED_CAPS_0.
    cap_add: [SYS_ADMIN, NET_ADMIN, SYS_PTRACE]
    command:
      - |
        apt-get update && apt-get install -y --no-install-recommends git bubblewrap strace && \
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
        "security_opt": ["seccomp:unconfined", "apparmor:unconfined"],
        "cap_add": ["SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE"],
        "support": "non-root setuid bwrap REQUIRED_CAPS_0",
        "production_recommendation": False,
    }
