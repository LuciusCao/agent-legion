import { INTERACTION_TYPE_LABELS } from "../labels";
import type { InteractionStats } from "../types";

export function seconds(value: number): string {
  const minutes = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  return `${minutes}:${secs.toString().padStart(2, "0")}`;
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return map[char] ?? char;
  });
}

export function formatDuration(ms: number): string {
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

export function formatInteractionStats(stats: Record<string, InteractionStats> | undefined): string {
  if (!stats) return "";
  const parts: string[] = [];
  const order = ["example_practice", "interaction_summary", "video_summary"];
  for (const type of order) {
    if (stats[type]) {
      const label = INTERACTION_TYPE_LABELS[type] || type;
      const { passed, total } = stats[type];
      parts.push(`${label} ${passed}/${total}`);
    }
  }
  for (const [type, { passed, total }] of Object.entries(stats)) {
    if (!order.includes(type)) {
      const label = INTERACTION_TYPE_LABELS[type] || type;
      parts.push(`${label} ${passed}/${total}`);
    }
  }
  return parts.join(" ｜ ");
}
