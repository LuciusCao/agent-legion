import { useWorkspaceApiTokensQuery } from '../../hooks/useWorkspaceApiTokensQuery'
import { ApiAccessInfoCard } from './ApiAccessInfoCard'
import { ApiAccessReference } from './ApiAccessReference'
import { WorkspaceApiTokensSection } from './WorkspaceApiTokensSection'
import styles from './ApiAccess.module.css'

/**
 * workspace 设置「外部对接」一级 section 的正文（#870）：外部系统（CMS /
 * 表单 / 定时任务 / 其他 agent）经 workspace API Token 提交条目、轮询状态、
 * 下载产物的全部接入面——接入参数、Token 签发与吊销、端点清单、示例、
 * 下载说明。API Token 管理是 admin 动作（后端 require_admin），由页面按
 * 角色挂载本 section。
 */
export function ApiAccessSection({ workspaceId }: { workspaceId: string }) {
  const { data } = useWorkspaceApiTokensQuery(workspaceId)
  const apiBase = window.location.origin

  return (
    <>
      <p className={styles.lead}>
        外部系统凭 API Token 免登录调用本 workspace：提交条目创建 job、
        轮询运行状态、下载产物。Token 只绑定当前
        workspace，只能调用下方列出的端点。
      </p>
      <ApiAccessInfoCard
        workspaceId={workspaceId}
        apiBase={apiBase}
        rateLimit={data?.rate_limit}
      />
      <WorkspaceApiTokensSection workspaceId={workspaceId} />
      <ApiAccessReference workspaceId={workspaceId} apiBase={apiBase} />
    </>
  )
}
