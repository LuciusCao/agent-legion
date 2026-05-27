import { useEffect, useRef, useCallback } from "react";
import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";
import { useVideoEvents } from "../hooks/useVideoEvents";
import { AgentPanel } from "../components/AgentPanel";
import { StatCards } from "../components/StatCards";
import { VideoList } from "../components/VideoList";
import { BatchToolbar } from "../components/BatchToolbar";
import { PackageToolbar } from "../components/PackageToolbar";
import { AddDialog } from "../components/AddDialog";

export function ListPage() {
  const {
    selectedType,
    searchQuery,
    setSelectedType,
    setSearchQuery,
    toggleSelectMode,
    togglePackageSelectMode,
    selectMode,
    packageSelectMode,
    fetchVideos,
    sseConnected,
  } = useVideoStore();
  const { openAddDialog, showToast } = useUiStore();

  const handleFetchVideos = useCallback(async () => {
    await fetchVideos();
    const err = useVideoStore.getState().error;
    if (err) {
      showToast(`加载失败: ${err}`, "error");
      useVideoStore.getState().clearError();
    }
  }, [fetchVideos, showToast]);
  const tabsRef = useRef<(HTMLElement & { activeTabIndex: number }) | null>(null);

  useEffect(() => {
    handleFetchVideos();
  }, [handleFetchVideos]);

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
          value={searchQuery}
          onInput={(e: React.FormEvent<HTMLElement>) => setSearchQuery((e.target as HTMLInputElement).value)}
        />
      </section>

      <div className="stats-row">
        <StatCards />
        <div className="stats-actions">
          {!sseConnected && (
            <span className="sse-status" title="实时连接已断开，正在尝试重连…">
              <md-icon style={{ color: "var(--md-sys-color-error)" }}>cloud_off</md-icon>
            </span>
          )}
          <md-icon-button onClick={handleFetchVideos} title="刷新">
            <md-icon>refresh</md-icon>
          </md-icon-button>
          <md-icon-button onClick={toggleSelectMode} title={selectMode ? "完成" : "多选"} className={selectMode ? "active-icon" : ""}>
            <md-icon>{selectMode ? "close" : "checklist"}</md-icon>
          </md-icon-button>
          <md-icon-button onClick={togglePackageSelectMode} title={packageSelectMode ? "完成" : "打包"} className={packageSelectMode ? "active-icon" : ""}>
            <md-icon>{packageSelectMode ? "close" : "inventory_2"}</md-icon>
          </md-icon-button>
          <md-icon-button onClick={openAddDialog} title="添加">
            <md-icon>add</md-icon>
          </md-icon-button>
        </div>
      </div>
      {packageSelectMode ? <PackageToolbar /> : <BatchToolbar />}
      <VideoList />
      <AddDialog />
    </section>
  );
}
