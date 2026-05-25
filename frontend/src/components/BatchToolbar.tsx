import { useState } from "react";
import { useVideoStore } from "../stores/videoStore";
import { useUiStore } from "../stores/uiStore";
import { BatchRerunDialog } from "./BatchRerunDialog";
import { BatchDeleteDialog } from "./BatchDeleteDialog";

export function BatchToolbar() {
  const {
    selectedIds,
    selectMode,
    toggleSelectMode,
    clearSelection,
    selectAllVisible,
    batchDelete,
    fetchVideos,
  } = useVideoStore();
  const { showToast } = useUiStore();

  const [rerunDialogOpen, setRerunDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);

  if (!selectMode) return null;

  const count = selectedIds.size;
  const hasSelection = count > 0;

  const handleDeleteConfirm = async () => {
    const result = await batchDelete(Array.from(selectedIds));
    const succeeded = result.results.filter((r) => r.status === "deleted").length;
    const failed = result.results.length - succeeded;
    showToast(
      failed > 0
        ? `删除完成：成功 ${succeeded} 项，失败 ${failed} 项`
        : `删除完成：成功 ${succeeded} 项`,
      failed > 0 ? "error" : "success",
    );
    clearSelection();
    setDeleteDialogOpen(false);
    await fetchVideos();
  };

  const handleRerun = () => {
    if (!hasSelection) return;
    setRerunDialogOpen(true);
  };

  const handleDelete = () => {
    if (!hasSelection) return;
    setDeleteDialogOpen(true);
  };

  return (
    <>
      <div className="batch-toolbar card-elevated">
        <span>已选择 {count} 项</span>
        <div className="batch-actions">
          <md-text-button onClick={selectAllVisible}>全选</md-text-button>
          <md-text-button onClick={clearSelection}>取消选择</md-text-button>
          <md-icon-button disabled={(!hasSelection) || undefined} onClick={handleRerun} title="重跑">
            <md-icon>restart_alt</md-icon>
          </md-icon-button>
          <md-icon-button disabled={(!hasSelection) || undefined} style={{ color: "var(--md-sys-color-error)" }} onClick={handleDelete} title="删除">
            <md-icon>delete</md-icon>
          </md-icon-button>
          <md-outlined-button onClick={toggleSelectMode}>退出</md-outlined-button>
        </div>
      </div>
      <BatchRerunDialog
        open={rerunDialogOpen}
        videoIds={Array.from(selectedIds)}
        onClose={() => setRerunDialogOpen(false)}
      />
      <BatchDeleteDialog
        open={deleteDialogOpen}
        count={count}
        onClose={() => setDeleteDialogOpen(false)}
        onConfirm={handleDeleteConfirm}
      />
    </>
  );
}
