import { useVideoStore } from "../stores/videoStore";

export function PackageToolbar() {
  const {
    selectedIds,
    togglePackageSelectMode,
    selectPackageAll,
    selectPackageUnpacked,
    clearSelection,
    batchPackage,
    fetchVideos,
  } = useVideoStore();

  const count = selectedIds.size;
  const hasSelection = count > 0;

  const handlePackage = async () => {
    if (!hasSelection) return;
    const result = await batchPackage(Array.from(selectedIds));
    window.location.href = result.download_url;
    togglePackageSelectMode();
    await fetchVideos();
  };

  return (
    <div className="batch-toolbar card-elevated">
      <span>已选择 {count} 项</span>
      <div className="batch-actions">
        <md-text-button onClick={selectPackageAll}>全选</md-text-button>
        <md-text-button onClick={selectPackageUnpacked}>仅选未打包</md-text-button>
        <md-text-button onClick={clearSelection}>取消选择</md-text-button>
        <md-icon-button disabled={(!hasSelection) || undefined} onClick={handlePackage} title="打包">
          <md-icon>inventory_2</md-icon>
        </md-icon-button>
        <md-outlined-button onClick={togglePackageSelectMode}>退出</md-outlined-button>
      </div>
    </div>
  );
}
