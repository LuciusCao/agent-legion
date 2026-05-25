import { useEffect, useMemo, useState } from "react";
import type { PhaseRun, TranscriptionRun, ContentType } from "../types";
import { KNOWLEDGE_PHASES, PHASE_LABELS, QUESTION_PHASES } from "../labels";

const STATUS_ICONS: Record<string, string> = {
  completed: "check_circle",
  running: "sync",
  failed: "error",
  queued: "schedule",
  pending: "radio_button_unchecked",
};

const STATUS_TEXT: Record<string, string> = {
  completed: "已完成",
  running: "处理中",
  failed: "失败",
  queued: "排队中",
  pending: "待处理",
};

interface PhaseRunsPanelProps {
  phaseRuns: PhaseRun[];
  transcriptionRuns: TranscriptionRun[];
  contentType?: ContentType;
}

interface TimelineItem {
  run: PhaseRun;
  label: string;
  tool: string;
  queueTime: number;
  processTime: number;
  occurrence?: number;
}

function buildItem(
  run: PhaseRun,
  prevRun: PhaseRun | null,
  now: number,
  transcriptionRuns: TranscriptionRun[]
): TimelineItem {
  const started = new Date(run.started_at).getTime();
  const finished = run.finished_at ? new Date(run.finished_at).getTime() : null;
  const prevFinished = prevRun?.finished_at ? new Date(prevRun.finished_at).getTime() : null;

  let queueTime = 0;
  if (prevFinished) {
    queueTime = Math.max(0, started - prevFinished);
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
      tool = cmd[0] || "";
    } catch {
      tool = "";
    }
  }

  return { run, label: PHASE_LABELS[run.phase_key], tool, queueTime, processTime };
}

export function PhaseRunsPanel({ phaseRuns, transcriptionRuns, contentType }: PhaseRunsPanelProps) {
  const [now, setNow] = useState(Date.now());
  const [viewMode, setViewMode] = useState<"latest" | "history">("latest");
  const [expandedDetails, setExpandedDetails] = useState<Set<number>>(new Set());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const allPhases = contentType === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;

  const { latestItems, historyItems, summary } = useMemo(() => {
    const sorted = [...phaseRuns].sort((a, b) => a.id - b.id);

    // --- latestItems: each phase's latest record, in pipeline order ---
    const latestByPhase = new Map<string, PhaseRun>();
    for (const run of sorted) {
      const existing = latestByPhase.get(run.phase_key);
      if (!existing || run.id > existing.id) {
        latestByPhase.set(run.phase_key, run);
      }
    }

    const latestRun = sorted[sorted.length - 1];
    const latestPhaseIndex = latestRun ? allPhases.indexOf(latestRun.phase_key) : -1;
    const currentPhases = latestPhaseIndex >= 0 ? allPhases.slice(0, latestPhaseIndex + 1) : [];

    const latestItems: TimelineItem[] = [];
    for (const phase of currentPhases) {
      const run = latestByPhase.get(phase);
      if (run) {
        const prev = latestItems.length > 0 ? latestItems[latestItems.length - 1].run : null;
        latestItems.push(buildItem(run, prev, now, transcriptionRuns));
      }
    }

    // --- historyItems: all records flat, with occurrence count ---
    const phaseOccurrence = new Map<string, number>();
    const historyItems: TimelineItem[] = [];
    for (let i = 0; i < sorted.length; i++) {
      const run = sorted[i];
      const count = (phaseOccurrence.get(run.phase_key) || 0) + 1;
      phaseOccurrence.set(run.phase_key, count);
      const prev = i > 0 ? sorted[i - 1] : null;
      historyItems.push({ ...buildItem(run, prev, now, transcriptionRuns), occurrence: count });
    }

    // --- summary: always based on latest record of each phase ---
    const latestRuns = latestItems.map((item) => item.run);
    const completedCount = latestRuns.filter((r) => r.status === "completed").length;

    const hasRunning = latestRuns.some((r) => r.status === "running");
    const hasFailed = latestRuns.some((r) => r.status === "failed");
    const isCompleted = completedCount >= allPhases.length;

    let status: string;
    if (hasRunning) status = "running";
    else if (hasFailed) status = "failed";
    else if (isCompleted) status = "completed";
    else status = "pending";

    return {
      latestItems,
      historyItems,
      summary: { completedCount, totalCount: allPhases.length, status },
    };
  }, [phaseRuns, transcriptionRuns, now, allPhases]);

  function formatDuration(ms: number): string {
    if (ms <= 0) return "—";
    const sec = Math.floor(ms / 1000);
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    if (m >= 60) {
      const h = Math.floor(m / 60);
      return `${h}时${m % 60}分${s}秒`;
    }
    return m > 0 ? `${m}分${s}秒` : `${s}秒`;
  }

  function toggleDetail(runId: number) {
    setExpandedDetails((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  }

  const transPrimary = transcriptionRuns.find((t) => t.status !== "fallback") || transcriptionRuns[0];
  const transFallback = transcriptionRuns.find((t) => t.status === "fallback");

  const items = viewMode === "latest" ? latestItems : historyItems;

  return (
    <div className="phase-runs-panel">
      {summary.totalCount > 0 && (
        <div className="phase-runs-summary">
          <div className="summary-main">
            <span className="summary-count">
              {summary.completedCount} / {summary.totalCount}
            </span>
            <span className="summary-label">阶段完成</span>
          </div>
          <div className="summary-meta">
            <md-text-button onClick={() => setViewMode((v) => (v === "latest" ? "history" : "latest"))}>
              {viewMode === "latest" ? "历史" : "当前"}
            </md-text-button>
          </div>
        </div>
      )}

      {items.length === 0 && <p className="empty-state">暂无处理记录</p>}

      <div className="phase-timeline">
        <div className="phase-timeline-items">
          {items.map((item, idx) => {
            const icon = STATUS_ICONS[item.run.status] || "help";
            const statusText = STATUS_TEXT[item.run.status] || item.run.status;
            const hasError = !!item.run.error_message;
            const isTranscribe = item.run.phase_key === "transcribe";
            const hasTransDetails = isTranscribe && transcriptionRuns.length > 0;
            const isDetailExpanded = expandedDetails.has(item.run.id);
            const canExpand = hasError || hasTransDetails;

            return (
              <div key={item.run.id} className="phase-timeline-item">
                <div className="timeline-left">
                  <div
                    className={`timeline-node status-${item.run.status} ${
                      item.run.status === "running" ? "spinning" : ""
                    }`}
                  >
                    <md-icon>{icon}</md-icon>
                  </div>
                  {idx < items.length - 1 && <div className="timeline-line" />}
                </div>

                <div className={`timeline-content status-${item.run.status}`}>
                  <div className="timeline-header">
                    <span className="timeline-name">
                      {item.label}
                      {item.occurrence && item.occurrence > 1 ? (
                        <span className="occurrence-badge"> 第{item.occurrence}次</span>
                      ) : null}
                    </span>
                    <span className={`timeline-status-badge ${item.run.status}`}>{statusText}</span>
                  </div>

                  {item.tool && (
                    <div className="timeline-meta">
                      <md-icon className="meta-icon">build_circle</md-icon>
                      <span className="timeline-tool">{item.tool}</span>
                    </div>
                  )}

                  <div className="timeline-times">
                    <span>
                      <md-icon className="meta-icon">schedule</md-icon>
                      排队 {formatDuration(item.queueTime)}
                    </span>
                    <span>
                      <md-icon className="meta-icon">timer</md-icon>
                      处理 {formatDuration(item.processTime)}
                    </span>
                  </div>

                  {canExpand && (
                    <button className="timeline-detail-toggle" onClick={() => toggleDetail(item.run.id)}>
                      {hasError ? (
                        <>
                          <md-icon className="toggle-icon">error</md-icon>
                          错误详情
                        </>
                      ) : (
                        <>
                          <md-icon className="toggle-icon">text_fields</md-icon>
                          转录详情
                        </>
                      )}
                      <md-icon className="toggle-icon">
                        {isDetailExpanded ? "expand_less" : "expand_more"}
                      </md-icon>
                    </button>
                  )}

                  {isDetailExpanded && hasError && (
                    <div className="timeline-detail-content error">{item.run.error_message}</div>
                  )}

                  {isDetailExpanded && hasTransDetails && (
                    <div className="timeline-detail-content transcription">
                      <div className="trans-row">
                        <span className="trans-key">Provider</span>
                        <span className="trans-value">{transPrimary?.provider || "—"}</span>
                      </div>
                      {transPrimary && transPrimary.srt_entry_count > 0 && (
                        <div className="trans-row">
                          <span className="trans-key">字幕条目</span>
                          <span className="trans-value">{transPrimary.srt_entry_count}</span>
                        </div>
                      )}
                      {transPrimary?.validation_summary && (
                        <div className="trans-row">
                          <span className="trans-key">验证结果</span>
                          <span className="trans-value">{transPrimary.validation_summary}</span>
                        </div>
                      )}
                      {transFallback?.fallback_reason && (
                        <div className="trans-row">
                          <span className="trans-key">Fallback</span>
                          <span className="trans-value">{transFallback.fallback_reason}</span>
                        </div>
                      )}
                      {transcriptionRuns.length > 1 && (
                        <div className="trans-row">
                          <span className="trans-key">尝试次数</span>
                          <span className="trans-value">{transcriptionRuns.length}</span>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
