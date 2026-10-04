"""Git transport for editing snapshots: bounded output and one shared deadline."""

import os
import selectors
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from server.app.services.skill_repo_edit import SkillEditValidationError

SNAPSHOT_SECONDS = 20


class SnapshotGit:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.deadline = time.monotonic() + SNAPSHOT_SECONDS

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("editing snapshot deadline exceeded")
        return remaining

    def run(
        self, args: list[str], limit: int, payload: bytes = b"", *, missing_ok: bool = False
    ) -> bytes:
        """Drain stdout while feeding stdin; never buffer beyond limit + one byte.

        Own a process group so interrupted IO kills descendants and reaps Git.
        stderr is discarded: diagnostics must not expose local repository data.
        """
        try:
            self.remaining()
            with subprocess.Popen(
                ["git", "-C", str(self.repo), *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            ) as process:
                try:
                    assert process.stdin is not None and process.stdout is not None
                    with selectors.DefaultSelector() as selector:
                        os.set_blocking(process.stdout.fileno(), False)
                        selector.register(process.stdout, selectors.EVENT_READ)
                        pending = memoryview(payload)
                        if pending:
                            os.set_blocking(process.stdin.fileno(), False)
                            selector.register(process.stdin, selectors.EVENT_WRITE)
                        else:
                            process.stdin.close()
                        output = bytearray()
                        while selector.get_map():
                            for key, event in selector.select(self.remaining()):
                                if event == selectors.EVENT_WRITE:
                                    count = os.write(key.fd, pending[:65536])
                                    pending = pending[count:]
                                    if not pending:
                                        selector.unregister(key.fileobj)
                                        process.stdin.close()
                                else:
                                    chunk = os.read(key.fd, min(65536, limit + 1 - len(output)))
                                    if not chunk:
                                        selector.unregister(key.fileobj)
                                    output.extend(chunk)
                                    if len(output) > limit:
                                        raise ValueError("Git output exceeds snapshot budget")
                        code = process.wait(timeout=self.remaining())
                        if missing_ok and code == 1:
                            return b""
                        if code != 0:
                            raise ValueError("Git could not read the editing snapshot")
                        return bytes(output)
                finally:
                    # Do not signal a PID after wait() reaped it: it may be reused.
                    if process.returncode is None:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise SkillEditValidationError(
                "Cannot export a complete Git editing snapshot",
                [
                    {
                        "path": ".",
                        "error": "Git snapshot exceeded resource limits or could not be read",
                    }
                ],
            ) from exc


def batch_blobs(reader: SnapshotGit, entries: list[tuple[str, str, int]]) -> list[bytes]:
    """Validate the length-framed batch protocol against preflight tree metadata."""
    request = b"".join(oid.encode("ascii") + b"\n" for _, oid, _ in entries)
    limit = sum(size + len(oid) + 32 for _, oid, size in entries)
    raw = reader.run(["cat-file", "--batch"], limit, request)
    blobs = []
    offset = 0
    for _, oid, size in entries:
        header = f"{oid} blob {size}\n".encode("ascii")
        if raw[offset : offset + len(header)] != header:
            raise ValueError("Git batch header disagrees with tree metadata")
        offset += len(header)
        end = offset + size
        if raw[end : end + 1] != b"\n":
            raise ValueError("incomplete Git batch blob")
        blobs.append(raw[offset:end])
        offset = end + 1
    if offset != len(raw):
        raise ValueError("unexpected trailing Git batch output")
    return blobs
