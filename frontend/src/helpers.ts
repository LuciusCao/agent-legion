import type { ContentType, VideoItem } from "./types";

export type VideoFilter = {
  selectedType: ContentType;
  statusFilter: string;
  searchQuery: string;
};

export function statusGroup(video: VideoItem): string {
  if (video.status === "missing_url" || video.current_phase === "waiting_for_url") return "missing_url";
  if (video.status === "failed") return "failed";
  if (video.status === "completed") return "completed";
  if (video.status === "running") return "running";
  return "queued";
}

export function filterVideos(videos: VideoItem[], filter: VideoFilter): VideoItem[] {
  const query = filter.searchQuery.trim().toLowerCase();
  return videos.filter((video) => {
    if (video.content_type !== filter.selectedType) return false;
    if (filter.statusFilter !== "all" && statusGroup(video) !== filter.statusFilter) return false;
    if (!query) return true;
    return [video.id, video.title, video.external_id].some((value) =>
      value.toLowerCase().includes(query),
    );
  });
}

export function visibleSelectedIds(visibleVideos: VideoItem[], selectedIds: Set<string>): string[] {
  const visibleIds = new Set(visibleVideos.map((video) => video.id));
  return Array.from(selectedIds).filter((id) => visibleIds.has(id));
}

export function getInteractionQuestion(node: Record<string, unknown>): Record<string, unknown> {
  return node.question && typeof node.question === "object"
    ? (node.question as Record<string, unknown>)
    : node;
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

export function seconds(value: number): string {
  const minutes = Math.floor(value / 60);
  const secs = Math.floor(value % 60);
  return `${minutes}:${secs.toString().padStart(2, "0")}`;
}
