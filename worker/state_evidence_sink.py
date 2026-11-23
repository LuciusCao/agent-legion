"""Lease-bound, append-only emergency event sink for reactor write failures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from worker.state_evidence_lines import render_line

if TYPE_CHECKING:
    from shared.redaction import SecretRedactor

EMERGENCY_ATTEMPT_EVENT = "worker_attempt_start"
_MAX_DUMP_TOTAL_BYTES = 64 * 1024 * 1024


class EmergencyEventsSink:
    """Append-only, per-line-redacted sink for one stream's diverted events
    (#1147 pump write failure). The first batch carries a lease boundary;
    legacy/unidentified streams without one remain observable but cannot
    prove completion for result recovery (#1350).

    Per-batch open-append-close (binary): crash-safe, no handle shared
    across pool tasks. One line's render/redact/ENCODE failure is dropped
    in isolation — never the batch (review P3-2: the encode used to sit
    outside the per-line guard, so a lone-surrogate line killed its whole
    batch; delivery's per-line semantics apply here too). Both one line
    (after redaction) and the sink's total in-process growth are capped,
    so the emergency path cannot fill the state volume. All failures are
    swallowed after logging — the sink is an observation face, never a
    control-flow input."""

    def __init__(self, path: Path, redactor: SecretRedactor, lease_id: str = "") -> None:
        self.path = path
        self._redactor = redactor
        self._lease_id = lease_id
        self._attempt_written = False
        self._written = 0
        self._capped = False

    def write_lines(self, lines: list[bytes]) -> None:
        if self._capped or not lines:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as dst:
                if self._lease_id and not self._attempt_written:
                    lines = [
                        json.dumps(
                            {"type": EMERGENCY_ATTEMPT_EVENT, "lease_id": self._lease_id}
                        ).encode("utf-8"),
                        *lines,
                    ]
                    self._attempt_written = True
                for line in lines:
                    try:
                        payload = render_line(line, self._redactor).encode("utf-8")
                    except Exception:
                        # #204 broad-except audit: 单行渲染/脱敏/编码任一逃逸
                        # （span 函数抛出，或 lone surrogate 的
                        # UnicodeEncodeError；机制同 shared/pi_events.
                        # _write_kept_event 的 fail-closed）——整行丢弃，绝不
                        # 把 raw 行写进 state 目录，也绝不因一行炸掉整批应急
                        # 转储（delivery 侧同款单行失败语义）。结果空间：转储
                        # 缺该行。日志保全：print。
                        print("emergency dump dropped an unrenderable line", flush=True)
                        continue
                    if self._written + len(payload) > _MAX_DUMP_TOTAL_BYTES:
                        self._capped = True
                        print(
                            f"emergency events dump capped at {_MAX_DUMP_TOTAL_BYTES}"
                            f" bytes: {self.path}",
                            flush=True,
                        )
                        return
                    dst.write(payload)
                    self._written += len(payload)
        except Exception as exc:
            # #204 broad-except audit: 应急转储写失败（state 目录不可写、
            # 磁盘满）是纯观测面降级——吞掉只记日志，事件面损失与既有
            # 行为（注销流）一致但不更糟；转储失败不得让 parse-pool 任务
            # 异常逃逸（那会注销流并丢掉后续全部事件）。日志保全：print。
            print(f"emergency events dump write failed for {self.path}: {exc!r}", flush=True)
