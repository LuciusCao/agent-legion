import { useEffect, useMemo, useState } from "react";
import type { PhaseRun, TranscriptionRun, ContentType } from "../types";
import { PHASE_LABELS } from "../labels";

const KNOWLEDGE_PHASES = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "interaction_generate",
  "content_review",
  "assemble",
  "package",
];

const QUESTION_PHASES = [
  "download",
  "transcribe",
  "subtitle_review",
  "chapter_generate",
  "assemble",
  "package",
];

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

interface RoundItem {
  run: PhaseRun;
  label: string;
  tool: string;
  queueTime: number;
  processTime: number;
}

interface Round {
  index: number;
  items: RoundItem[];
  isLatest: boolean;
}

export function PhaseRunsPanel({ phaseRuns, transcriptionRuns, contentType }: PhaseRunsPanelProps) {
  const [now, setNow] = useState(Date.now());
  const [expandedRounds, setExpandedRounds] = useState<Set<number>>(new Set());
  const [expandedDetails, setExpandedDetails] = useState<Set<number>>(new Set());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const allPhases = contentType === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;

  const { rounds, summary } = useMemo(() => {
    const sorted = [...phaseRuns].sort((a, b) => a.id - b.id);

    // Simplified round grouping: a new round starts when a phase repeats
    const roundRuns: PhaseRun[][] = [];
    let currentRound: PhaseRun[] = [];
    const seen = new Set<string>();

    for (const run of sorted) {
      if (seen.has(run.phase_key)) {
        if (currentRound.length > 0) roundRuns.push(currentRound);
        currentRound = [];
        seen.clear();
      }
      seen.add(run.phase_key);
      currentRound.push(run);
    }
    if (currentRound.length > 0) roundRuns.push(currentRound);

    const rounds: Round[] = roundRuns.map((runs, roundIndex) => ({
      index: roundIndex,
      isLatest: roundIndex === roundRuns.length - 1,
      items: runs.map((run, idxInRound) => {
        const prevFinished = idxInRound > 0 ? runs[idxInRound - 1].finished_at : null;
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
          run,
          label: PHASE_LABELS[run.phase_key],
          tool,
          queueTime,
          processTime,
        };
      }),
    }));

    // Summary from latest round
    const latestRound = roundRuns[roundRuns.length - 1] || [];
    const completedCount = latestRound.filter((r) => r.status === "completed").length;

    let totalTime = 0;
    if (sorted.length > 0) {
      const earliestStart = new Date(sorted[0].started_at).getTime();
      const latestEnd = sorted.reduce((max, run) => {
        if (run.finished_at) {
          return Math.max(max, new Date(run.finished_at).getTime());
        }
        return max;
      }, now);
      totalTime = latestEnd - earliestStart;
    }

    const hasRunning = sorted.some((r) => r.status === "running");
    const hasFailed = sorted.some((r) => r.status === "failed");
    const isCompleted = completedCount >= allPhases.length;

    let status: string;
    if (hasRunning) status = "running";
    else if (hasFailed) status = "failed";
    else if (isCompleted) status = "completed";
    else status = "pending";

    return {
      rounds,
      summary: { completedCount, totalCount: allPhases.length, totalTime, status },
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

  function toggleRound(roundIndex: number) {
    setExpandedRounds((prev) => {
      const next = new Set(prev);
      if (next.has(roundIndex)) next.delete(roundIndex);
      else next.add(roundIndex);
      return next;
    });
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
            <span className="summary-time">{formatDuration(summary.totalTime)}</span>
            <span className={`summary-status-badge ${summary.status}`}>
              {STATUS_TEXT[summary.status] || summary.status}
            </span>
          </div>
        </div>
      )}

      {rounds.length === 0 && <p className="empty-state">暂无处理记录</p>}

      <div className="phase-timeline">
        {rounds.map((round) => {
          const isExpanded = round.isLatest || expandedRounds.has(round.index);
          const label = round.isLatest ? "最新一轮" : `第 ${round.index + 1} 轮`;
          return (
            <div key={round.index} className="phase-round">
              <button
                className="phase-round-header"
                onClick={() => toggleRound(round.index)}
                disabled={round.isLatest}
              >
                <span className="round-label">{label}</span>
                <span className="round-meta">
                  {round.items.length} 个阶段
                  {!round.isLatest && (
                    <md-icon>{isExpanded ? "expand_less" : "expand_more"}</md-icon>
                  )}
                </span>
              </button>

              {isExpanded && (
                <div className="phase-timeline-items">
                  {round.items.map((item, idxInRound) => {
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
                          {idxInRound < round.items.length - 1 && (
                            <div className="timeline-line" />
                          )}
                        </div>

                        <div className={`timeline-content status-${item.run.status}`}>
                          <div className="timeline-header">
                            <span className="timeline-name">{item.label}</span>
                            <span className={`timeline-status-badge ${item.run.status}`}>
                              {statusText}
                            </span>
                          </div>

                          <div className="timeline-meta">
                            <md-icon className="meta-icon">build_circle</md-icon>
                            <span className="timeline-tool">{item.tool}</span>
                          </div>

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
                            <button
                              className="timeline-detail-toggle"
                              onClick={() => toggleDetail(item.run.id)}
                            >
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
                            <div className="timeline-detail-content error">
                              {item.run.error_message}
                            </div>
                          )}

                          {isDetailExpanded && hasTransDetails && (
                            <div className="timeline-detail-content transcription">
                              <div className="trans-row">
                                <span className="trans-key">Provider</span>
                                <span className="trans-value">
                                  {transPrimary?.provider || "—"}
                                </span>
                              </div>
                              {transPrimary && transPrimary.srt_entry_count > 0 && (
                                <div className="trans-row">
                                  <span className="trans-key">字幕条目</span>
                                  <span className="trans-value">
                                    {transPrimary.srt_entry_count}
                                  </span>
                                </div>
                              )}
                              {transPrimary?.validation_summary && (
                                <div className="trans-row">
                                  <span className="trans-key">验证结果</span>
                                  <span className="trans-value">
                                    {transPrimary.validation_summary}
                                  </span>
                                </div>
                              )}
                              {transFallback?.fallback_reason && (
                                <div className="trans-row">
                                  <span className="trans-key">Fallback</span>
                                  <span className="trans-value">
                                    {transFallback.fallback_reason}
                                  </span>
                                </div>
                              )}
                              {transcriptionRuns.length > 1 && (
                                <div className="trans-row">
                                  <span className="trans-key">尝试次数</span>
                                  <span className="trans-value">
                                    {transcriptionRuns.length}
                                  </span>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
