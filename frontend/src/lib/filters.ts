import type { ContentType, VideoItem } from "../types";

export type VideoFilter = {
  selectedType: ContentType;
  statusFilter: string;
  searchQuery: string;
  packedFilter?: "all" | "packed" | "unpacked";
};

export function statusGroup(video: VideoItem): string {
  if (video.status === "missing_url" || video.current_phase === "waiting_for_url") return "failed";
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
    if (filter.packedFilter === "packed" && !video.packed) return false;
    if (filter.packedFilter === "unpacked" && !!video.packed) return false;
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
