import { useDetailStore } from "../stores/detailStore";

export function MetadataPanel() {
  const { artifacts } = useDetailStore();
  const meta = artifacts.metadata;

  if (!meta) return <div className="empty-state">暂无元数据</div>;

  return (
    <div className="tab-panel card-outlined">
      <pre>{JSON.stringify(meta, null, 2)}</pre>
    </div>
  );
}
