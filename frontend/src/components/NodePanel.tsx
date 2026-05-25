import { useDetailStore } from "../stores/detailStore";
import { INTERACTION_TYPE_LABELS } from "../labels";

interface NodePanelProps {
  onSeek?: (time: number) => void;
  replayInteraction?: (index: number) => void;
}

export function NodePanel({ onSeek, replayInteraction }: NodePanelProps) {
  const { artifacts, triggeredNodeIndexes } = useDetailStore();
  const nodes = artifacts.interactions;

  return (
    <div className="tab-panel">
      {nodes.map((node, i) => {
        const answered = triggeredNodeIndexes.has(i);
        const triggerTime = Number(node.trigger_time ?? 0);
        const typeLabel = INTERACTION_TYPE_LABELS[String(node.type ?? "")] || String(node.type ?? "");
        return (
          <div
            key={i}
            className={`node-card card-outlined ${answered ? "answered" : ""}`}
            onClick={() => {
              onSeek?.(triggerTime);
              replayInteraction?.(i);
            }}
            style={{ cursor: "pointer" }}
          >
            <div className="node-main">
              <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--md-sys-color-primary)" }}>
                {formatTime(triggerTime)}
              </span>
              <span>{node.instruction || "交互节点"}</span>
              <md-assist-chip label={typeLabel} />
            </div>
            {node.options && node.options.length > 0 && (
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "8px" }}>
                {node.options.map((opt, j) => (
                  <md-outlined-button key={j} disabled={answered || undefined}>{opt.text}</md-outlined-button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
