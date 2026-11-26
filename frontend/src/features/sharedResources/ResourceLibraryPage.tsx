import { useState } from 'react'
import { Button } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import { AppBar } from '../../components/AppBar'
import { AppShell } from '../../layouts/AppShell'
import { listSharedResources } from '../../api/sharedResources'
import { useCurrentWorkspace, useWorkspaces } from '../../hooks/useWorkspaces'
import { isResourceAccessError } from './sharedResourceError'
import { sharedResourceKeys } from './sharedResourceQueryKeys'
import { useWorkspaceAccess } from './useWorkspaceAccess'
import { ResourceSourceDialog } from './ResourceSourceDialog'
import { ShareResourceDialog } from './ShareResourceDialog'
import type { SharedResource } from './sharedResourceTypes'
import styles from './SharedResources.module.css'

export default function ResourceLibraryPage() {
  const { workspaceId = '' } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const currentWorkspace = useCurrentWorkspace()
  const workspacesQuery = useWorkspaces()
  const access = useWorkspaceAccess(workspaceId)
  const [createOpen, setCreateOpen] = useState(false)
  const [sharing, setSharing] = useState<SharedResource | null>(null)
  const resourcesQuery = useQuery({
    queryKey: sharedResourceKeys.list(workspaceId),
    queryFn: ({ signal }) => listSharedResources(workspaceId, signal),
    enabled: Boolean(workspaceId),
  })
  const resources = resourcesQuery.data?.resources ?? []
  const sourceName = (resource: SharedResource) => {
    if (resource.owner_workspace_id === workspaceId) {
      return currentWorkspace?.name ?? '当前工作区'
    }
    return (
      workspacesQuery.data?.find(
        (workspace) => workspace.id === resource.owner_workspace_id
      )?.name ?? '其他工作区'
    )
  }

  const content = () => {
    if (resourcesQuery.isLoading) {
      return <div className={styles.state}>正在加载资源库…</div>
    }
    if (resourcesQuery.isError) {
      return (
        <div className={styles.state} role="alert">
          <h2 className={styles.stateTitle}>
            {isResourceAccessError(resourcesQuery.error)
              ? '无法查看这个工作区的资源'
              : '资源库加载失败'}
          </h2>
          <p>
            {isResourceAccessError(resourcesQuery.error)
              ? '你没有查看权限，或工作区已经不存在。'
              : '请检查网络后重试。'}
          </p>
          <Button onClick={() => void resourcesQuery.refetch()}>
            重新加载
          </Button>
        </div>
      )
    }
    if (resources.length === 0) {
      return (
        <div className={styles.state}>
          <h2 className={styles.stateTitle}>资源库还是空的</h2>
          <p>
            {access.canEdit
              ? '可以从已有材料或文件夹创建第一份共享资源。'
              : '当前工作区还没有共享资源。'}
          </p>
          {access.canEdit && (
            <Button variant="outlined" onClick={() => setCreateOpen(true)}>
              从材料或文件夹创建
            </Button>
          )}
        </div>
      )
    }
    return (
      <div className={styles.resourceList}>
        {resources.map((resource) => {
          const stopped = resource.access_status === 'revoked'
          return (
            <article className={styles.resourceCard} key={resource.id}>
              <div>
                <div className={styles.resourceHeading}>
                  <h2 className={styles.resourceName}>{resource.name}</h2>
                  {stopped && (
                    <span
                      className={`${styles.status} ${styles.warningStatus}`}
                    >
                      已停止新任务使用
                    </span>
                  )}
                </div>
                <p className={styles.source}>来自 {sourceName(resource)}</p>
              </div>
              <div className={styles.actions}>
                <Button
                  variant="contained"
                  disabled={stopped || !access.isAdmin}
                  title={
                    stopped
                      ? '这个资源已停止用于新任务，已有任务不受影响'
                      : !access.isAdmin
                        ? '只有管理员可以在 Workflow Studio 中编辑工作流'
                        : undefined
                  }
                  onClick={() =>
                    navigate(
                      `/workspaces/${workspaceId}/workflow-studio?resource=${encodeURIComponent(resource.id)}`
                    )
                  }
                >
                  添加到工作流
                </Button>
              </div>
              <div className={`${styles.actions} ${styles.secondaryActions}`}>
                <Button onClick={() => setSharing(resource)}>共享</Button>
                <Button
                  onClick={() =>
                    navigate(
                      `/workspaces/${workspaceId}/resources/${encodeURIComponent(resource.id)}`
                    )
                  }
                >
                  管理
                </Button>
              </div>
            </article>
          )
        })}
      </div>
    )
  }

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title={`${currentWorkspace?.name || workspaceId} / 资源库`}
          backTo={`/workspaces/${workspaceId}`}
          scrolled={scrolled}
        />
      )}
      mainClassName={styles.main}
    >
      <div className={styles.page}>
        <header className={styles.header}>
          <div>
            <h1 className={styles.title}>资源库</h1>
            <p className={styles.subtitle}>
              把共享资源添加到工作流，然后创建运行。
            </p>
          </div>
          {resources.length > 0 && access.canEdit && (
            <Button onClick={() => setCreateOpen(true)}>
              从材料或文件夹创建
            </Button>
          )}
        </header>
        {access.isViewer && (
          <p className={styles.notice}>
            你在当前工作区为只读成员，可以查看和共享资源，不能更改版本或撤销已有共享。
          </p>
        )}
        {access.error && (
          <p className={`${styles.notice} ${styles.warningNotice}`}>
            无法确认编辑权限，已按只读方式显示。
          </p>
        )}
        {content()}
      </div>
      {createOpen && (
        <ResourceSourceDialog
          workspaceId={workspaceId}
          onClose={() => setCreateOpen(false)}
          onCreated={() => undefined}
        />
      )}
      {sharing && (
        <ShareResourceDialog
          workspaceId={workspaceId}
          resource={sharing}
          canEdit={access.canEdit}
          onClose={() => setSharing(null)}
        />
      )}
    </AppShell>
  )
}
