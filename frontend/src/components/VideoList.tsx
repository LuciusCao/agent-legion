import { useMemo, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useVideoStore } from "../stores/videoStore";
import { PHASE_LABELS, STATUS_LABELS, TYPE_LABELS } from "../labels";
import { statusGroup } from "../helpers";
import { PhaseStepper } from "./PhaseStepper";

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
  const checkboxRefs = useRef<Map<string, HTMLElement>>(new Map());

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

  useEffect(() => {
    filtered.forEach((v) => {
      const el = checkboxRefs.current.get(v.id);
      if (el) {
        (el as any).checked = selectedIds.has(v.id);
      }
    });
  }, [selectedIds, filtered]);

  if (filtered.length === 0) {
    return (
      <div className="video-list">
        <div className="empty-state">
          <md-icon style={{ fontSize: "48px", color: "var(--md-sys-color-outline)" }}>inbox</md-icon>
          <p className="title-medium">暂无{selectedType === "knowledge" ? "知识点" : "题目"}视频</p>
          <p className="body-medium" style={{ color: "var(--md-sys-color-outline)" }}>
            点击右上角 + 添加视频
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="video-list">
      <md-list>
        {filtered.map((video) => {
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
                <md-checkbox
                  slot="start"
                  ref={(el: HTMLElement | null) => {
                    if (el) checkboxRefs.current.set(video.id, el);
                    else checkboxRefs.current.delete(video.id);
                  }}
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    toggleVideoSelection(video.id);
                  }}
                />
              )}
              <div slot="headline" className="resource-title-row">
                <strong>{video.title || "未命名"}</strong>
                <PhaseStepper video={video} />
              </div>
              <div slot="supporting-text">
                <small>
                  {TYPE_LABELS[video.content_type]} · {video.external_id || "未填 ID"} · {PHASE_LABELS[video.current_phase] || video.current_phase}
                </small>
                {video.error_message && (
                  <small className="error-text" title={video.error_message}>
                    {video.error_message}
                  </small>
                )}
              </div>
              <div slot="end" className="status-end">
                <md-assist-chip label={STATUS_LABELS[statusGroup(video)] || video.status} />
              </div>
            </md-list-item>
          );
        })}
      </md-list>
    </div>
  );
}
