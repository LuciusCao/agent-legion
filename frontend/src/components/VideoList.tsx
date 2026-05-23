import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useVideoStore } from "../stores/videoStore";
import { PHASE_LABELS, STATUS_LABELS, TYPE_LABELS } from "../labels";
import { statusGroup } from "../helpers";
import type { VideoItem } from "../types";

const GROUP_ORDER = ["queued", "running", "failed", "completed"];

export function VideoList() {
  const navigate = useNavigate();
  const {
    videos,
    selectedType,
    statusFilter,
    searchQuery,
    selectMode,
    selectedIds,
    toggleVideoSelection,
  } = useVideoStore();

  const filtered = useMemo(() => {
    return videos.filter((v) => {
      if (v.content_type !== selectedType) return false;
      if (statusFilter !== "all" && statusGroup(v) !== statusFilter) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const haystack = `${v.external_id} ${v.title} ${v.id}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [videos, selectedType, statusFilter, searchQuery]);

  const grouped = useMemo(() => {
    const map: Record<string, VideoItem[]> = {};
    GROUP_ORDER.forEach((k) => (map[k] = []));
    filtered.forEach((v) => {
      const group = statusGroup(v);
      if (!map[group]) map[group] = [];
      map[group].push(v);
    });
    return map;
  }, [filtered]);

  return (
    <div className="grouped-list">
      {GROUP_ORDER.map((group) => {
        const items = grouped[group] || [];
        if (items.length === 0) return null;
        return (
          <div key={group} className="resource-group card-outlined">
            <div className="group-header">
              <h2>{STATUS_LABELS[group] || group}</h2>
              <span className="label-small">{items.length} 项</span>
            </div>
            <md-list>
              {items.map((video) => {
                const isSelected = selectedIds.has(video.id);
                return (
                  <md-list-item
                    key={video.id}
                    type="button"
                    className={isSelected ? "active" : ""}
                    onClick={() => {
                      if (selectMode) {
                        toggleVideoSelection(video.id);
                      } else {
                        navigate(`/videos/${video.id}`);
                      }
                    }}
                  >
                    {selectMode && (
                      <md-checkbox slot="start" checked={isSelected} />
                    )}
                    <div slot="headline" className="resource-main">
                      <strong>{video.title || "未命名"}</strong>
                      <small>
                        {TYPE_LABELS[video.content_type]} · {video.external_id || "未填 ID"} · {PHASE_LABELS[video.current_phase] || video.current_phase}
                      </small>
                    </div>
                    <md-assist-chip slot="end" label={STATUS_LABELS[statusGroup(video)] || video.status} />
                  </md-list-item>
                );
              })}
            </md-list>
          </div>
        );
      })}
    </div>
  );
}
