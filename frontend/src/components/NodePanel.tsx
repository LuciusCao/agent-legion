import { useDetailStore } from "../stores/detailStore";

export function NodePanel() {
  const { artifacts, triggeredNodeIndexes } = useDetailStore();
  const nodes = artifacts.interactions;

  return (
    <div className="tab-panel">
      {nodes.map((node, i) => {
        const answered = triggeredNodeIndexes.has(i);
        return (
          <div key={i} className={`node-card card-outlined ${answered ? "answered" : ""}`}>
            <div className="node-main">
              <span style={{ fontVariantNumeric: "tabular-nums" }}>
                {formatTime(Number(node.trigger_time ?? 0))}
              </span>
              <span>{node.content?.question || "交互节点"}</span>
              <md-assist-chip label={String(node.node_type ?? node.type ?? "")} />
            </div>
            {node.content?.options && (
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "8px" }}>
                {node.content.options.map((opt: string, j: number) => (
                  <md-outlined-button key={j} disabled={answered}>{opt}</md-outlined-button>
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
