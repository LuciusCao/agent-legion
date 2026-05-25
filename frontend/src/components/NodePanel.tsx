import { useArtifactStore } from "../stores/artifactStore";
import { useInteractionStore } from "../stores/interactionStore";
import { INTERACTION_TYPE_LABELS } from "../labels";
import styles from "./NodePanel.module.css";

interface ReviewEntry {
  item_id: string;
  status: string;
  issues?: Array<{ title?: string; details?: string }>;
}

interface ReviewResult {
  status?: string;
  reviews?: ReviewEntry[];
}

function getReviewMap(review: unknown): Map<string, ReviewEntry> {
  const map = new Map<string, ReviewEntry>();
  if (!review || typeof review !== "object") return map;
  const r = review as ReviewResult;
  if (Array.isArray(r.reviews)) {
    for (const entry of r.reviews) {
      if (entry.item_id) {
        map.set(entry.item_id, entry);
      }
    }
  }
  return map;
}

function getGlobalStatus(review: unknown): string | undefined {
  if (!review || typeof review !== "object") return undefined;
  return (review as ReviewResult).status;
}

function formatIssue(issue: { title?: string; details?: string }): string {
  if (issue.title && issue.details) return `${issue.title}：${issue.details}`;
  return issue.details || issue.title || "";
}

const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  published: { text: "已通过", color: "#2e7d32" },
  pending_review: { text: "待审", color: "#ed6c02" },
  rejected: { text: "驳回", color: "#ba1a1a" },
};

interface NodePanelProps {
  onSeek?: (time: number) => void;
  replayInteraction?: (index: number) => void;
}

export function NodePanel({ onSeek, replayInteraction }: NodePanelProps) {
  const { artifacts } = useArtifactStore();
  const { triggeredNodeIndexes } = useInteractionStore();
  const nodes = artifacts.interactions;
  const reviewMap = getReviewMap(artifacts.review);
  const globalStatus = getGlobalStatus(artifacts.review);

  return (
    <div className="tab-panel">
      {nodes.map((node, i) => {
        const answered = triggeredNodeIndexes.has(i);
        const triggerTime = Number(node.trigger_time ?? 0);
        const typeLabel = INTERACTION_TYPE_LABELS[String(node.type ?? "")] || String(node.type ?? "");
        const nodeId = String(node.id ?? "");
        const nodeReview = nodeId ? reviewMap.get(nodeId) : undefined;
        const status = nodeReview?.status || globalStatus;
        const statusInfo = status ? STATUS_LABELS[status] : undefined;
        const issues = nodeReview?.issues?.filter((issue) => formatIssue(issue)) ?? [];

        return (
          <div
            key={node.id ?? i}
            className={`${styles.nodeCard} card-outlined ${answered ? styles.answered : ""}`}
            onClick={() => {
              onSeek?.(triggerTime);
              replayInteraction?.(i);
            }}
            style={{ cursor: "pointer" }}
          >
            <div className={styles.nodeMain}>
              <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--md-sys-color-primary)" }}>
                {formatTime(triggerTime)}
              </span>
              <span>{node.instruction || "交互节点"}</span>
              <md-assist-chip label={typeLabel} />
            </div>
            {node.options && node.options.length > 0 && (
              <div
                style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "8px" }}
                onClick={(e) => e.stopPropagation()}
              >
                {node.options.map((opt, j) => (
                  <md-outlined-button key={opt.id ?? j} disabled={answered || undefined}>{opt.text}</md-outlined-button>
                ))}
              </div>
            )}
            {statusInfo && (
              <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "4px" }}>
                <span
                  style={{
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    color: statusInfo.color,
                  }}
                >
                  {statusInfo.text}
                </span>
                {issues.length > 0 && (
                  <ul style={{ margin: 0, paddingLeft: "16px", fontSize: "0.75rem", color: "var(--md-sys-color-on-surface-variant)" }}>
                    {issues.map((issue, idx) => (
                      <li key={idx}>{formatIssue(issue)}</li>
                    ))}
                  </ul>
                )}
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
