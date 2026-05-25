import { useEffect, useMemo, useState } from "react";
import type { PhaseRun, TranscriptionRun } from "../types";
import { PHASE_LABELS } from "../labels";

interface PhaseRunsPanelProps {
  phaseRuns: PhaseRun[];
  transcriptionRuns: TranscriptionRun[];
}

export function PhaseRunsPanel({ phaseRuns, transcriptionRuns }: PhaseRunsPanelProps) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const items = useMemo(() => {
    const sorted = [...phaseRuns].sort((a, b) => a.id - b.id);
    return sorted.map((run, index) => {
      const prevFinished = sorted[index - 1]?.finished_at;
      const started = new Date(run.started_at).getTime();
      const finished = run.finished_at ? new Date(run.finished_at).getTime() : null;

      let queueTime = 0;
      if (prevFinished) {
        queueTime = Math.max(0, started - new Date(prevFinished).getTime());
      }

      let processTime = 0;
      if (finished) {
        processTime = finished - started;
      } else if (run.status === "running") {
        processTime = now - started;
      }

      let tool = "";
      if (run.phase_key === "transcribe") {
        const tr = transcriptionRuns.find((t) => t.status !== "fallback");
        tool = tr?.provider || "transcribe";
      } else {
        try {
          const cmd = JSON.parse(run.command_json) as string[];
          tool = cmd[0] || run.phase_key;
        } catch {
          tool = run.phase_key;
        }
      }

      return {
        ...run,
        label: PHASE_LABELS[run.phase_key] || run.phase_key,
        tool,
        queueTime,
        processTime,
      };
    });
  }, [phaseRuns, transcriptionRuns, now]);

  function formatDuration(ms: number): string {
    if (ms <= 0) return "—";
    const sec = Math.floor(ms / 1000);
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m > 0 ? `${m}分${s}秒` : `${s}秒`;
  }

  return (
    <div className="phase-runs-panel">
      <h3>处理记录</h3>
      {items.length === 0 && <p className="empty-state">暂无处理记录</p>}
      <div className="phase-runs-list">
        {items.map((item) => (
          <div key={item.id} className={`phase-run-item status-${item.status}`}>
            <div className="phase-run-header">
              <span className="phase-run-name">{item.label}</span>
              <span className={`phase-run-status ${item.status}`}>{item.status}</span>
            </div>
            <div className="phase-run-meta">
              <span className="phase-run-tool">{item.tool}</span>
            </div>
            <div className="phase-run-times">
              <span>排队 {formatDuration(item.queueTime)}</span>
              <span>处理 {formatDuration(item.processTime)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
