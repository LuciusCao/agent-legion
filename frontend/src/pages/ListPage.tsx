import { useEffect, useRef } from "react";
import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";
import { useVideoEvents } from "../hooks/useVideoEvents";
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
    toggleSelectMode,
    selectMode,
    fetchVideos,
  } = useVideoStore();
  const { openAddDialog } = useUiStore();
  const tabsRef = useRef<(HTMLElement & { activeTabIndex: number }) | null>(null);

  useEffect(() => {
    fetchVideos();
  }, [fetchVideos]);

  useVideoEvents();

  useEffect(() => {
    const tabs = tabsRef.current;
    if (!tabs) return;
    const handleChange = () => {
      setSelectedType(tabs.activeTabIndex === 0 ? "knowledge" : "question");
    };
    tabs.addEventListener("change", handleChange);
    return () => tabs.removeEventListener("change", handleChange);
  }, [setSelectedType]);

  return (
    <section className="view workbench-view">
      <AgentPanel />

      <section className="filters-row">
        <md-tabs
          ref={tabsRef}
          active-tab-index={selectedType === "knowledge" ? 0 : 1}
        >
          <md-primary-tab>知识点</md-primary-tab>
          <md-primary-tab>题目</md-primary-tab>
        </md-tabs>
        <md-outlined-text-field
          type="search"
          placeholder="搜索 ID、标题或内部记录"
          onInput={(e: React.FormEvent<HTMLElement>) => setSearchQuery((e.target as HTMLInputElement).value)}
        />
      </section>

      <div className="stats-row">
        <StatCards />
        <div className="stats-actions">
          <md-icon-button onClick={() => fetchVideos()} title="刷新">
            <md-icon>refresh</md-icon>
          </md-icon-button>
          <md-icon-button onClick={toggleSelectMode} title={selectMode ? "完成" : "多选"} className={selectMode ? "active-icon" : ""}>
            <md-icon>{selectMode ? "close" : "checklist"}</md-icon>
          </md-icon-button>
          <md-icon-button onClick={openAddDialog} title="添加">
            <md-icon>add</md-icon>
          </md-icon-button>
        </div>
      </div>
      <BatchToolbar />
      <VideoList />
      <AddDialog />
    </section>
  );
}
