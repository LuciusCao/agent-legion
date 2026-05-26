import { useEffect, useMemo, useState } from "react";
import type { PhaseRun, TranscriptionRun, ContentType, VideoItem } from "../types";
import { KNOWLEDGE_PHASES, PHASE_LABELS, QUESTION_PHASES, STATUS_LABELS, STATUS_ICONS } from "../labels";
import { formatDuration } from "../lib/formatters";
import { PhaseStepper } from "./PhaseStepper";
import { TranscriptionDetails } from "./TranscriptionDetails";
import styles from "./PhaseRunsPanel.module.css";


interface PhaseRunsPanelProps {
  phaseRuns: PhaseRun[];
  transcriptionRuns: TranscriptionRun[];
  video?: VideoItem | null;
  contentType?: ContentType;
  currentPhase?: string;
  videoStatus?: string;
}

interface TimelineItem {
  run: PhaseRun;
  label: string;
  tool: string;
  queueTime: number;
  processTime: number;
  occurrence?: number;
}

function formatTranscriptionProvider(provider: string | undefined): string {
  if (!provider) return "transcribe";
  const normalized = provider.toLowerCase();
  if (normalized === "whisper") return "whisper.cpp";
  if (normalized === "sensevoice") return "SenseVoice";
  return provider;
}

function extractOpenClawAgentName(commandJson: string): string {
  try {
    const command = JSON.parse(commandJson) as unknown;
    if (!Array.isArray(command)) return "";
    const parts = command.map((part) => String(part));
    for (let i = 0; i < parts.length; i++) {
      if (parts[i] === "--agent" && parts[i + 1]) {
        return parts[i + 1];
      }
      if (parts[i].startsWith("--agent=")) {
        return parts[i].slice("--agent=".length);
      }
    }
  } catch {
    return "";
  }
  return "";
}

function formatOpenClawAgentName(commandJson: string): string {
  const agentName = extractOpenClawAgentName(commandJson);
  return agentName ? `openclaw-${agentName}` : "";
}

function buildItem(
  run: PhaseRun,
  prevRun: PhaseRun | null,
  now: number,
  transcriptionRuns: TranscriptionRun[],
  resetQueueTime = false
): TimelineItem {
  const started = new Date(run.started_at).getTime();
  const finished = run.finished_at ? new Date(run.finished_at).getTime() : null;
  const prevFinished = prevRun?.finished_at ? new Date(prevRun.finished_at).getTime() : null;

  let queueTime = 0;
  if (!resetQueueTime && prevFinished) {
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
    tool = formatTranscriptionProvider(tr?.provider);
  } else {
    tool = formatOpenClawAgentName(run.command_json);
  }

  return { run, label: PHASE_LABELS[run.phase_key], tool, queueTime, processTime };
}

const NODE_STATUS_CLASS: Record<string, string> = {
  completed: styles.statusCompleted,
  running: styles.statusRunning,
  failed: styles.statusFailed,
};

const CONTENT_STATUS_CLASS: Record<string, string> = {
  completed: styles.statusCompleted,
  running: styles.statusRunning,
  failed: styles.statusFailed,
};

const BADGE_STATUS_CLASS: Record<string, string> = {
  completed: styles.completed,
  running: styles.running,
  failed: styles.failed,
  pending: styles.pending,
  queued: styles.queued,
};

export function PhaseRunsPanel({
  phaseRuns,
  transcriptionRuns,
  video,
  contentType,
  currentPhase,
  videoStatus,
}: PhaseRunsPanelProps) {
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
    const currentPhaseIndex = currentPhase ? allPhases.indexOf(currentPhase) : -1;
    const latestPhaseIndex = currentPhaseIndex >= 0
      ? currentPhaseIndex
      : latestRun
        ? allPhases.indexOf(latestRun.phase_key)
        : -1;
    const currentPhases = latestPhaseIndex >= 0 ? allPhases.slice(0, latestPhaseIndex + 1) : [];

    const latestItems: TimelineItem[] = [];
    for (const phase of currentPhases) {
      let run = latestByPhase.get(phase);
      const runId = run?.id;
      const phaseWasRerun = runId !== undefined ? sorted.some((candidate) => (
        candidate.phase_key === phase && candidate.id < runId
      )) : false;
      if (
        currentPhase === phase &&
        videoStatus &&
        ["queued", "running"].includes(videoStatus) &&
        run?.status !== videoStatus
      ) {
        run = {
          id: -1,
          video_id: run?.video_id ?? "",
          phase_key: phase,
          status: videoStatus,
          started_at: new Date(now).toISOString(),
          finished_at: null,
          command_json: run?.command_json ?? "[]",
          exit_code: null,
          log_path: "",
          error_message: "",
        };
      }
      if (run) {
        const prev = latestItems.length > 0 ? latestItems[latestItems.length - 1].run : null;
        latestItems.push(buildItem(run, prev, now, transcriptionRuns, phaseWasRerun || run.id < 0));
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
  }, [phaseRuns, transcriptionRuns, now, allPhases, currentPhase, videoStatus]);

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
    <div className={styles.phaseRunsPanel}>
      {video && (
        <div className={styles.panelStepper}>
          <PhaseStepper video={video} />
        </div>
      )}

      {summary.totalCount > 0 && (
        <div className={styles.phaseRunsSummary}>
          <div className={styles.summaryMain}>
            <span className={styles.summaryCount}>
              {summary.completedCount} / {summary.totalCount}
            </span>
            <span className={styles.summaryLabel}>阶段完成</span>
          </div>
          <div className={styles.summaryMeta}>
            <md-text-button onClick={() => setViewMode((v) => (v === "latest" ? "history" : "latest"))}>
              {viewMode === "latest" ? "历史" : "当前"}
            </md-text-button>
          </div>
        </div>
      )}

      {items.length === 0 && <p className="empty-state">暂无处理记录</p>}

      <div className={styles.phaseTimeline}>
        <div className={styles.phaseTimelineItems}>
          {items.map((item, idx) => {
            const icon = STATUS_ICONS[item.run.status] || "help";
            const statusText = STATUS_LABELS[item.run.status] || item.run.status;
            const hasError = !!item.run.error_message;
            const isTranscribe = item.run.phase_key === "transcribe";
            const hasTransDetails = isTranscribe && transcriptionRuns.length > 0;
            const isDetailExpanded = expandedDetails.has(item.run.id);
            const canExpand = hasError || hasTransDetails;

            return (
              <div key={item.run.id} className={styles.phaseTimelineItem}>
                <div className={styles.timelineLeft}>
                  <div
                    className={`${styles.timelineNode} ${NODE_STATUS_CLASS[item.run.status] || ""} ${
                      item.run.status === "running" ? styles.spinning : ""
                    }`}
                  >
                    <md-icon>{icon}</md-icon>
                  </div>
                  {idx < items.length - 1 && <div className={styles.timelineLine} />}
                </div>

                <div className={`${styles.timelineContent} ${CONTENT_STATUS_CLASS[item.run.status] || ""}`}>
                  <div className={styles.timelineHeader}>
                    <span className={styles.timelineName}>
                      {item.label}
                      {item.occurrence && item.occurrence > 1 ? (
                        <span className={styles.occurrenceBadge}> 第{item.occurrence}次</span>
                      ) : null}
                    </span>
                    <span className={`${styles.timelineStatusBadge} ${BADGE_STATUS_CLASS[item.run.status] || ""}`}>{statusText}</span>
                  </div>

                  {item.tool && (
                    <div className={styles.timelineMeta}>
                      <md-icon className={styles.metaIcon}>build_circle</md-icon>
                      <span className={styles.timelineTool}>{item.tool}</span>
                    </div>
                  )}

                  <div className={styles.timelineTimes}>
                    <span>
                      <md-icon className={styles.metaIcon}>schedule</md-icon>
                      排队 {formatDuration(item.queueTime)}
                    </span>
                    <span>
                      <md-icon className={styles.metaIcon}>timer</md-icon>
                      处理 {formatDuration(item.processTime)}
                    </span>
                  </div>

                  {canExpand && (
                    <button className={styles.timelineDetailToggle} onClick={() => toggleDetail(item.run.id)}>
                      {hasError ? (
                        <>
                          <md-icon className={styles.toggleIcon}>error</md-icon>
                          错误详情
                        </>
                      ) : (
                        <>
                          <md-icon className={styles.toggleIcon}>text_fields</md-icon>
                          转录详情
                        </>
                      )}
                      <md-icon className={styles.toggleIcon}>
                        {isDetailExpanded ? "expand_less" : "expand_more"}
                      </md-icon>
                    </button>
                  )}

                  {isDetailExpanded && hasError && (
                    <div className="timeline-detail-content error">{item.run.error_message}</div>
                  )}

                  {isDetailExpanded && hasTransDetails && (
                    <TranscriptionDetails
                      primary={transPrimary}
                      fallback={transFallback}
                      totalCount={transcriptionRuns.length}
                    />
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
