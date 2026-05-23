import { useEffect } from "react";
import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";
import { STATUS_LABELS } from "../labels";
import { AgentPanel } from "../components/AgentPanel";
import { StatCards } from "../components/StatCards";
import { VideoList } from "../components/VideoList";
import { BatchToolbar } from "../components/BatchToolbar";
import { AddDialog } from "../components/AddDialog";

export function ListPage() {
  const {
    selectedType,
    setSelectedType,
    setSearchQuery,
    statusFilter,
    toggleSelectMode,
    selectMode,
    fetchVideos,
  } = useVideoStore();
  const { openAddDialog, agents } = useUiStore();

  useEffect(() => {
    fetchVideos();
  }, [fetchVideos]);

  return (
    <section className="view workbench-view">
      <header className="topbar">
        <div>
          <h1>Video Hive</h1>
          <p className="title-medium">资源处理队列</p>
        </div>
        <div className="topbar-actions">
          <md-icon-button onClick={() => fetchVideos()}>
            <md-icon>refresh</md-icon>
          </md-icon-button>
          <md-text-button onClick={toggleSelectMode}>
            {selectMode ? "完成" : "多选"}
          </md-text-button>
          <md-fab onClick={openAddDialog}>
            <md-icon slot="icon">add</md-icon>
          </md-fab>
        </div>
      </header>

      {agents.length > 0 && <AgentPanel />}

      <section className="filters-row">
        <md-tabs>
          <md-primary-tab active={selectedType === "knowledge"} onClick={() => setSelectedType("knowledge")}>
            知识点
          </md-primary-tab>
          <md-primary-tab active={selectedType === "question"} onClick={() => setSelectedType("question")}>
            题目
          </md-primary-tab>
        </md-tabs>
        <md-outlined-text-field
          type="search"
          placeholder="搜索 ID、标题或内部记录"
          onInput={(e) => setSearchQuery((e.target as HTMLInputElement).value)}
        />
        <md-chip-set>
          {["all", "missing_url", "queued", "running", "failed", "completed"].map((s) => (
            <md-filter-chip
              key={s}
              label={s === "all" ? "全部" : (STATUS_LABELS[s] || s)}
              selected={statusFilter === s}
              onClick={() => useVideoStore.getState().setStatusFilter(s)}
            />
          ))}
        </md-chip-set>
      </section>

      <StatCards />
      <BatchToolbar />
      <VideoList />
      <AddDialog />
    </section>
  );
}
