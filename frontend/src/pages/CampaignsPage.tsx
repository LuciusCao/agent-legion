import { useParams } from 'react-router-dom'
import { useCampaigns } from '../hooks/useCampaigns'
import { toErrorMessage } from '../lib/queryError'
import { BatchList } from '../components/campaign/BatchList'

/**
 * workspace 的「批量任务」页（#532 PR-D）：列表（最新在前，行展开详情）
 * + 新建向导入口。数据由 useCampaigns 轮询（存在活跃批量任务时每 5s）。
 * 路由保持 /workspaces/:id/campaigns（URL 稳定），页面名对用户是「批量任务」。
 */
export default function CampaignsPage() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const { data, isLoading, error } = useCampaigns(workspaceId)

  if (!workspaceId) return null
  return (
    <BatchList
      workspaceId={workspaceId}
      campaigns={data?.campaigns ?? []}
      loading={isLoading}
      error={toErrorMessage(error)}
    />
  )
}
