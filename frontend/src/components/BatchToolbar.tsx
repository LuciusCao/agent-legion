import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";

export function BatchToolbar() {
  const {
    selectedIds,
    selectMode,
    toggleSelectMode,
    clearSelection,
    selectAllVisible,
    batchDelete,
    batchPackage,
    batchRerun,
    fetchVideos,
  } = useVideoStore();
  const { showToast } = useUiStore();

  if (!selectMode) return null;

  const count = selectedIds.size;
  const hasSelection = count > 0;

  const handleDelete = async () => {
    if (!hasSelection) return;
    if (!window.confirm(`确定删除 ${count} 个资源？`)) return;
    const result = await batchDelete(Array.from(selectedIds));
    const succeeded = result.results.filter((r) => r.status === "deleted").length;
    showToast(`删除完成：成功 ${succeeded} 项`, "success");
    clearSelection();
    await fetchVideos();
  };

  const handlePackage = async () => {
    if (!hasSelection) return;
    const result = await batchPackage(Array.from(selectedIds));
    window.location.href = result.download_url;
  };

  const handleRerun = async () => {
    if (!hasSelection) return;
    const result = await batchRerun(Array.from(selectedIds), "download");
    const succeeded = result.results.filter((r) => r.status === "rerun").length;
    showToast(`重跑完成：成功 ${succeeded} 项`, "success");
    clearSelection();
    await fetchVideos();
  };

  return (
    <div className="batch-toolbar card-elevated">
      <span>已选择 {count} 项</span>
      <div className="batch-actions">
        <md-outlined-button onClick={toggleSelectMode}>退出多选</md-outlined-button>
        <md-text-button onClick={selectAllVisible}>全选</md-text-button>
        <md-text-button onClick={clearSelection}>取消</md-text-button>
        <md-icon-button disabled={(!hasSelection) || undefined} onClick={handleRerun} title="重跑">
          <md-icon>restart_alt</md-icon>
        </md-icon-button>
        <md-icon-button disabled={(!hasSelection) || undefined} onClick={handlePackage} title="打包">
          <md-icon>inventory_2</md-icon>
        </md-icon-button>
        <md-icon-button disabled={(!hasSelection) || undefined} style={{ color: "var(--md-sys-color-error)" }} onClick={handleDelete} title="删除">
          <md-icon>delete</md-icon>
        </md-icon-button>
      </div>
    </div>
  );
}
