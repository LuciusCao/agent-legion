import { useVideoStore } from "../stores/videoStore";

export function PackageToolbar() {
  const {
    selectedIds,
    togglePackageSelectMode,
    selectPackageAll,
    selectPackageUnpacked,
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
        <md-text-button onClick={selectPackageUnpacked}>仅选择未打包</md-text-button>
        <md-outlined-button onClick={togglePackageSelectMode}>取消</md-outlined-button>
        <md-filled-button disabled={!hasSelection} onClick={handlePackage}>
          打包 {count > 0 ? `(${count})` : ""}
        </md-filled-button>
      </div>
    </div>
  );
}
