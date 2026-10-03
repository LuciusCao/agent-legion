"""Deduplicated timeline activity for local Kimi tasks (#772).

Activity warnings are observations, never inferred terminal transitions.
The watcher commits each event only after the durable append succeeds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from server.app.studio_chat.kimi_task_snapshot import BackgroundTask

QUIET_SECONDS = 120
_LABELS = {
    "created": "已创建",
    "starting": "正在启动",
    "running": "运行中",
    "awaiting_approval": "等待审批",
    "completed": "已完成",
    "failed": "失败",
    "killed": "已终止",
    "lost": "执行进程丢失",
    "timed_out": "已超时",
    "quiet": "长时间无输出，请检查进度",
    "stale": "心跳长时间未更新，请检查进程",
    "unavailable": "任务状态暂时无法读取，请检查 agent",
}


class BackgroundActivity:
    def __init__(self) -> None:
        self.tasks: dict[str, BackgroundTask] = {}
        self.first_seen: dict[str, float] = {}
        self.last_seen: dict[str, float] = {}
        self.reported: dict[str, str] = {}

    def updates(self, tasks: dict[str, BackgroundTask], now: float) -> list[dict[str, Any]]:
        self.tasks.update(tasks)
        for task_id in tasks:
            self.first_seen.setdefault(task_id, now)
            self.last_seen[task_id] = now
        events = []
        for task_id, task in self.tasks.items():
            first_seen = self.first_seen[task_id]
            status = task.status
            if not task.terminal:
                if now - self.last_seen[task_id] >= QUIET_SECONDS:
                    status = "unavailable"
                elif task.heartbeat_at and now - task.heartbeat_at >= QUIET_SECONDS:
                    status = "stale"
                elif (
                    now - (task.output_changed_at or task.started_at or first_seen) >= QUIET_SECONDS
                    and task.status == "running"
                ):
                    status = "quiet"
            if self.reported.get(task_id) == status:
                continue
            elapsed = max(0, int((task.finished_at or now) - (task.started_at or first_seen)))
            event = "background_task_finished" if task.terminal else "background_task_status"
            started = task.started_at or first_seen
            # Timestamp range validation lives here so malformed metadata
            # cannot take down the watcher or obscure other tasks.
            try:
                start_text = datetime.fromtimestamp(started, UTC).isoformat(timespec="seconds")
            except (ValueError, OverflowError, OSError):
                start_text = "未知"
            detail = f"后台任务「{task.description}」({task_id})：{_LABELS[status]}；开始 {start_text}，耗时 {elapsed} 秒"
            if task.terminal:
                detail += f"；结果摘要：{task.summary or '暂无输出摘要，可让助手核对结果'}"
            events.append(
                {
                    "event": event,
                    "task_id": task_id,
                    "kind": task.kind,
                    "status": status,
                    "task_status": task.status,
                    "description": task.description,
                    "started_at": started,
                    "elapsed_seconds": elapsed,
                    "summary": task.summary if task.terminal else "",
                    "detail": detail,
                }
            )
        return events

    def recorded(self, event: dict[str, Any]) -> None:
        task_id = str(event["task_id"])
        self.reported[task_id] = str(event["status"])
        if event["event"] == "background_task_finished":
            self.tasks.pop(task_id, None)
            self.first_seen.pop(task_id, None)
            self.last_seen.pop(task_id, None)
