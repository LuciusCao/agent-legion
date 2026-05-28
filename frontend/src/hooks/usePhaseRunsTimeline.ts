import { useEffect, useMemo, useState } from "react";
import type { PhaseRun, TranscriptionRun, ContentType } from "../types";
import { KNOWLEDGE_PHASES, PHASE_LABELS, QUESTION_PHASES } from "../labels";
import { formatDuration } from "../lib/formatters";
import { api } from "../api";

export interface UsePhaseRunsTimelineReturn {
  now: number;
  viewMode: "latest" | "history";
  setViewMode: (v: "latest" | "history") => void;
  expandedDetails: Set<number>;
  toggleDetail: (runId: number) => void;
  sessionLogs: Record<number, string>;
  openSession: (run: PhaseRun, sessionId: string) => Promise<void>;
  sessionDialog: { runId: number; sessionId: string; videoId: string } | null;
  setSessionDialog: (v: { runId: number; sessionId: string; videoId: string } | null) => void;
  transcriptionDialogOpen: boolean;
  setTranscriptionDialogOpen: (v: boolean) => void;
  items: TimelineItem[];
  sortedTrans: TranscriptionRun[];
  transPrimary?: TranscriptionRun;
  transFallback?: TranscriptionRun;
  formatDuration: (ms: number) => string;
  extractOpenClawArg: (commandJson: string, name: string) => string;
}

export interface TimelineItem {
  run: PhaseRun;
  label: string;
  tool: string;
  queueTime: number;
  processTime: number;
  occurrence?: number;
}

interface UsePhaseRunsTimelineProps {
  phaseRuns: PhaseRun[];
  transcriptionRuns: TranscriptionRun[];
  contentType?: ContentType;
  currentPhase?: string;
  videoStatus?: string;
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

function extractOpenClawArg(commandJson: string, name: string): string {
  try {
    const command = JSON.parse(commandJson) as unknown;
    if (!Array.isArray(command)) return "";
    const parts = command.map((part) => String(part));
    for (let i = 0; i < parts.length; i++) {
      if (parts[i] === name && parts[i + 1]) {
        return parts[i + 1];
      }
      if (parts[i].startsWith(`${name}=`)) {
        return parts[i].slice(name.length + 1);
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
    const tr = [...transcriptionRuns]
      .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())
      .find((t) => t.status !== "fallback");
    tool = formatTranscriptionProvider(tr?.provider);
  } else {
    tool = formatOpenClawAgentName(run.command_json);
  }

  return { run, label: PHASE_LABELS[run.phase_key], tool, queueTime, processTime };
}

export function usePhaseRunsTimeline({
  phaseRuns,
  transcriptionRuns,
  contentType,
  currentPhase,
  videoStatus,
}: UsePhaseRunsTimelineProps): UsePhaseRunsTimelineReturn {
  const [now, setNow] = useState(Date.now());
  const [viewMode, setViewMode] = useState<"latest" | "history">("latest");
  const [expandedDetails, setExpandedDetails] = useState<Set<number>>(new Set());
  const [sessionLogs, setSessionLogs] = useState<Record<number, string>>({});
  const [transcriptionDialogOpen, setTranscriptionDialogOpen] = useState(false);
  const [sessionDialog, setSessionDialog] = useState<{
    runId: number;
    sessionId: string;
    videoId: string;
  } | null>(null);

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const allPhases = contentType === "question" ? QUESTION_PHASES : KNOWLEDGE_PHASES;

  const { latestItems, historyItems } = useMemo(() => {
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

    return {
      latestItems,
      historyItems,
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

  async function openSession(run: PhaseRun, sessionId: string) {
    const runId = run.id;
    setSessionDialog({ runId, sessionId, videoId: run.video_id });
    if (sessionLogs[runId] || runId < 0) return;
    setSessionLogs((prev) => ({ ...prev, [runId]: "加载中..." }));
    try {
      const data = await api<{ log: string }>(`/api/videos/${run.video_id}/phase-runs/${runId}/session`);
      setSessionLogs((prev) => ({ ...prev, [runId]: data.log || "会话为空" }));
    } catch {
      setSessionLogs((prev) => ({ ...prev, [runId]: "会话文件暂不可用" }));
    }
  }

  const sortedTrans = [...transcriptionRuns].sort(
    (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
  );
  const transPrimary = sortedTrans[0];
  const transFallback = sortedTrans.find((t) => t.fallback_reason);

  const items = viewMode === "latest" ? latestItems : historyItems;

  return {
    now,
    viewMode,
    setViewMode,
    expandedDetails,
    toggleDetail,
    sessionLogs,
    openSession,
    sessionDialog,
    setSessionDialog,
    transcriptionDialogOpen,
    setTranscriptionDialogOpen,
    items,
    sortedTrans,
    transPrimary,
    transFallback,
    formatDuration,
    extractOpenClawArg,
  };
}
