import { useEffect, useMemo, useState } from "react";
import type { PhaseRun, TranscriptionRun } from "../types";
import { PHASE_LABELS } from "../labels";

interface PhaseRunsPanelProps {
  phaseRuns: PhaseRun[];
  transcriptionRuns: TranscriptionRun[];
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

export function PhaseRunsPanel({ phaseRuns, transcriptionRuns }: PhaseRunsPanelProps) {
  const [now, setNow] = useState(Date.now());
  const [expandedRounds, setExpandedRounds] = useState<Set<number>>(new Set());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const rounds = useMemo(() => {
    const sorted = [...phaseRuns].sort((a, b) => a.id - b.id);
    if (sorted.length === 0) return [];

    // Group runs into rounds: a new round starts when a phase repeats
    const rounds: Round[] = [];
    let currentRoundRuns: PhaseRun[] = [];
    let currentPhases = new Set<string>();

    for (const run of sorted) {
      if (currentPhases.has(run.phase_key)) {
        rounds.push({ index: rounds.length, items: [], isLatest: false });
        currentRoundRuns = [];
        currentPhases = new Set<string>();
      }
      currentPhases.add(run.phase_key);
      currentRoundRuns.push(run);
    }

    if (currentRoundRuns.length > 0) {
      rounds.push({ index: rounds.length, items: [], isLatest: false });
    }

    // Distribute sorted runs into rounds
    let runOffset = 0;
    for (let r = 0; r < rounds.length; r++) {
      const isLast = r === rounds.length - 1;
      const count = isLast
        ? sorted.length - runOffset
        : (() => {
            const seen = new Set<string>();
            for (let i = runOffset; i < sorted.length; i++) {
              if (seen.has(sorted[i].phase_key)) {
                return i - runOffset;
              }
              seen.add(sorted[i].phase_key);
            }
            return sorted.length - runOffset;
          })();

      const roundRuns = sorted.slice(runOffset, runOffset + count);
      runOffset += count;

      rounds[r].isLatest = r === rounds.length - 1;
      rounds[r].items = roundRuns.map((run, idxInRound) => {
        const prevFinished = idxInRound > 0 ? roundRuns[idxInRound - 1].finished_at : null;
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
          label: PHASE_LABELS[run.phase_key] || run.phase_key,
          tool,
          queueTime,
          processTime,
        };
      });
    }

    return rounds;
  }, [phaseRuns, transcriptionRuns, now]);

  function formatDuration(ms: number): string {
    if (ms <= 0) return "—";
    const sec = Math.floor(ms / 1000);
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m > 0 ? `${m}分${s}秒` : `${s}秒`;
  }

  function toggleRound(roundIndex: number) {
    setExpandedRounds((prev) => {
      const next = new Set(prev);
      if (next.has(roundIndex)) {
        next.delete(roundIndex);
      } else {
        next.add(roundIndex);
      }
      return next;
    });
  }

  return (
    <div className="phase-runs-panel">
      <h3>处理记录</h3>
      {rounds.length === 0 && <p className="empty-state">暂无处理记录</p>}
      <div className="phase-runs-list">
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
                <span>{label}</span>
                {!round.isLatest && (
                  <md-icon>{isExpanded ? "expand_less" : "expand_more"}</md-icon>
                )}
              </button>
              {isExpanded && (
                <div className="phase-round-items">
                  {round.items.map((item) => (
                    <div key={item.run.id} className={`phase-run-item status-${item.run.status}`}>
                      <div className="phase-run-header">
                        <span className="phase-run-name">{item.label}</span>
                        <span className={`phase-run-status ${item.run.status}`}>{item.run.status}</span>
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
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
