import { useNavigate } from 'react-router-dom'

/** Workspace 主页的 Studio 入口卡片：进入 workflow 草稿 / 对比 / 发布工作台。 */
export function StudioEntryCard({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate()
  return (
    <section
      style={{
        padding: 16,
        borderRadius: 12,
        background: '#ffffff',
        cursor: 'pointer',
      }}
      onClick={() => navigate(`/workspaces/${workspaceId}/workflow-studio`)}
    >
      <strong>进入 Studio</strong>
      <p style={{ margin: '4px 0 0', fontSize: 14, color: '#5f6368' }}>
        查看与编辑 workflow 草稿，对比并发布新版本。
      </p>
    </section>
  )
}
