import { useMemo } from "react";
import { useVideoStore } from "../stores/videoStore";
import { STATUS_LABELS } from "../labels";
import { statusGroup } from "../helpers";

const STATUSES = ["queued", "running", "failed", "completed"];

const STATUS_ICONS: Record<string, string> = {
  all: "inventory_2",
  queued: "schedule",
  running: "sync",
  failed: "error",
  completed: "check_circle",
};

export function StatCards() {
  const { videos, statusFilter, setStatusFilter } = useVideoStore();

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: videos.length };
    STATUSES.forEach((s) => {
      map[s] = videos.filter((v) => statusGroup(v) === s).length;
    });
    return map;
  }, [videos]);

  const items = [
    { key: "all", label: "全部" },
    ...STATUSES.map((s) => ({
      key: s,
      label: STATUS_LABELS[s] || s,
    })),
  ];

  return (
    <div className="stats-pills">
      {items.map((item) => (
        <div
          key={item.key}
          className={`stat-pill ${statusFilter === item.key ? "active" : ""}`}
          onClick={() => setStatusFilter(item.key)}
        >
          <md-icon>{STATUS_ICONS[item.key] || "help"}</md-icon>
          <span>{item.label}（{counts[item.key] ?? 0}）</span>
        </div>
      ))}
    </div>
  );
}
