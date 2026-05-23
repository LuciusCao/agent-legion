import { useDetailStore } from "../stores/detailStore";

export function MetadataPanel() {
  const { artifacts } = useDetailStore();
  const meta = artifacts.metadata;

  if (!meta) return <div className="empty-state">暂无元数据</div>;

  return (
    <md-outlined-card className="tab-panel">
      <pre>{JSON.stringify(meta, null, 2)}</pre>
    </md-outlined-card>
  );
}
