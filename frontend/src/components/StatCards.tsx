import { useMemo } from "react";
import { useVideoStore } from "../stores/videoStore";
import { STATUS_LABELS } from "../labels";

const STATUSES = ["missing_url", "queued", "running", "failed", "completed"];

export function StatCards() {
  const { videos, statusFilter, setStatusFilter } = useVideoStore();

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: videos.length };
    STATUSES.forEach((s) => {
      map[s] = videos.filter((v) => v.status === s).length;
    });
    return map;
  }, [videos]);

  const items = [
    { key: "all", label: "全部" },
    ...STATUSES.map((s) => ({ key: s, label: STATUS_LABELS[s] || s })),
  ];

  return (
    <div className="stats-panel">
      {items.map((item) => (
        <md-elevated-card
          key={item.key}
          className={`stat-card ${statusFilter === item.key ? "active" : ""}`}
          onClick={() => setStatusFilter(item.key)}
        >
          <strong>{counts[item.key] ?? 0}</strong>
          <span>{item.label}</span>
        </md-elevated-card>
      ))}
    </div>
  );
}
