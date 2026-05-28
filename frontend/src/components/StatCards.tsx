import { useMemo } from "react";
import { useVideoStore } from "../stores/videoStore";
import { STATUS_LABELS } from "../labels";
import { statusGroup } from "../helpers";
import styles from "./StatCards.module.css";

const STATUSES = ["queued", "running", "failed", "completed"];

const FILTER_ICONS: Record<string, string> = {
  all: "inventory_2",
  queued: "schedule",
  running: "sync",
  failed: "error",
  completed: "check_circle",
  packed: "archive",
  unpacked: "inventory_2",
};

export function StatCards() {
  const { videos, statusFilter, setStatusFilter, packedFilter, setPackedFilter } = useVideoStore();

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: videos.length };
    STATUSES.forEach((s) => {
      map[s] = videos.filter((v) => statusGroup(v) === s).length;
    });
    map.packed = videos.filter((v) => v.status === "completed" && v.packed).length;
    map.unpacked = videos.filter((v) => v.status === "completed" && !v.packed).length;
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
    <div className={styles.statsPills}>
      {items.map((item) => (
        <div
          key={item.key}
          className={`${styles.statPill} ${statusFilter === item.key ? styles.active : ""}`}
          onClick={() => setStatusFilter(item.key)}
        >
          <md-icon>{FILTER_ICONS[item.key] || "help"}</md-icon>
          <span>{item.label}（{counts[item.key] ?? 0}）</span>
        </div>
      ))}
      {statusFilter === "completed" && (
        <>
          <span className={styles.pillDivider} />
          <div
            className={`${styles.statPill} ${packedFilter === "packed" ? styles.active : ""}`}
            onClick={() => setPackedFilter(packedFilter === "packed" ? "all" : "packed")}
          >
            <md-icon>{FILTER_ICONS.packed}</md-icon>
            <span>已打包（{counts.packed ?? 0}）</span>
          </div>
          <div
            className={`${styles.statPill} ${packedFilter === "unpacked" ? styles.active : ""}`}
            onClick={() => setPackedFilter(packedFilter === "unpacked" ? "all" : "unpacked")}
          >
            <md-icon>{FILTER_ICONS.unpacked}</md-icon>
            <span>未打包（{counts.unpacked ?? 0}）</span>
          </div>
        </>
      )}
    </div>
  );
}
