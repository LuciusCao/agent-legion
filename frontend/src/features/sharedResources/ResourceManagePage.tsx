import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { AppBar } from '../../components/AppBar'
import { AppShell } from '../../layouts/AppShell'
import {
  deleteSharedResourceVersion,
  getSharedResource,
  listSharedResourceVersions,
  selectSharedResourceVersion,
} from '../../api/sharedResources'
import { useCurrentWorkspace } from '../../hooks/useWorkspaces'
import {
  isResourceAccessError,
  sharedResourceErrorMessage,
} from './sharedResourceError'
import { sharedResourceKeys } from './sharedResourceQueryKeys'
import { useWorkspaceAccess } from './useWorkspaceAccess'
import { ResourceSourceDialog } from './ResourceSourceDialog'
import { ShareResourceDialog } from './ShareResourceDialog'
import { ResourceVersionDiff } from './ResourceVersionDiff'
import { ResourceJobHistory } from './ResourceJobHistory'
import type { SharedResourceVersion } from './sharedResourceTypes'
import styles from './SharedResources.module.css'

export default function ResourceManagePage() {
  const { workspaceId = '', resourceId = '' } = useParams<{
    workspaceId: string
    resourceId: string
  }>()
  const currentWorkspace = useCurrentWorkspace()
  const queryClient = useQueryClient()
  const access = useWorkspaceAccess(workspaceId)
  const [createOpen, setCreateOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const [deleting, setDeleting] = useState<SharedResourceVersion | null>(null)
  const resourceQuery = useQuery({
    queryKey: sharedResourceKeys.detail(workspaceId, resourceId),
    queryFn: ({ signal }) => getSharedResource(workspaceId, resourceId, signal),
    enabled: Boolean(workspaceId && resourceId),
  })
  const versionsQuery = useQuery({
    queryKey: sharedResourceKeys.versions(workspaceId, resourceId),
    queryFn: ({ signal }) =>
      listSharedResourceVersions(workspaceId, resourceId, signal),
    enabled: Boolean(workspaceId && resourceId),
  })
  const resource = resourceQuery.data?.resource
  const versions = [...(versionsQuery.data?.versions ?? [])].sort(
    (a, b) => b.version - a.version
  )
  const currentVersion = resource?.current_selection?.version ?? null
  const stopped = resource?.access_status === 'revoked'

  const refreshResource = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: sharedResourceKeys.detail(workspaceId, resourceId),
      }),
      queryClient.invalidateQueries({
        queryKey: sharedResourceKeys.list(workspaceId),
      }),
      queryClient.invalidateQueries({
        queryKey: sharedResourceKeys.versions(workspaceId, resourceId),
      }),
    ])
  }
  const select = useMutation({
    mutationFn: (version: number) =>
      selectSharedResourceVersion(workspaceId, resourceId, {
        version,
        expectedRevision: resource?.current_selection?.revision ?? 0,
      }),
    onSuccess: async (result) => {
      setNotice(
        `之后创建的任务将使用 v${result.selection.version}，已有任务不变。`
      )
      await refreshResource()
    },
    onError: refreshResource,
  })
  const remove = useMutation({
    mutationFn: (version: number) =>
      deleteSharedResourceVersion(workspaceId, resourceId, version),
    onSuccess: async (_result, version) => {
      setDeleting(null)
      setNotice(`v${version} 已删除，已有任务使用的版本不会被改写。`)
      await refreshResource()
    },
  })
  const error = select.error ?? remove.error

  const body = () => {
    if (resourceQuery.isLoading || versionsQuery.isLoading) {
      return <div className={styles.state}>正在加载资源版本…</div>
    }
    if (resourceQuery.isError || versionsQuery.isError || !resource) {
      const loadError = resourceQuery.error ?? versionsQuery.error
      return (
        <div className={styles.state} role="alert">
          <h2 className={styles.stateTitle}>
            {isResourceAccessError(loadError)
              ? '无法管理这个资源'
              : '资源版本加载失败'}
          </h2>
          <p>
            {isResourceAccessError(loadError)
              ? '你没有查看权限，或资源已经不存在。'
              : '请返回资源库后重试。'}
          </p>
          <Button
            onClick={() => {
              void resourceQuery.refetch()
              void versionsQuery.refetch()
            }}
          >
            重新加载
          </Button>
        </div>
      )
    }
    return (
      <>
        <section className={styles.panel}>
          <div className={styles.resourceHeading}>
            <h2 className={styles.resourceName}>{resource.name}</h2>
            {stopped && (
              <span className={`${styles.status} ${styles.warningStatus}`}>
                已停止新任务使用
              </span>
            )}
          </div>
          <p className={styles.subtitle}>
            当前使用版本：
            {currentVersion === null ? '尚未选择' : `v${currentVersion}`}
          </p>
          <p className={styles.notice}>
            切换版本只影响之后创建的任务，已有任务始终保留当时使用的版本。
          </p>
          {!access.canEdit && (
            <p className={`${styles.notice} ${styles.warningNotice}`}>
              你在当前工作区为只读成员，只能查看版本和任务记录。
            </p>
          )}
          {stopped && (
            <p className={`${styles.notice} ${styles.warningNotice}`}>
              这个资源已停止用于新任务。已有任务仍可查看和继续执行。
            </p>
          )}
          <div className={styles.actions}>
            <Button onClick={() => setShareOpen(true)}>管理共享</Button>
            {resource.is_owner && access.canEdit && (
              <Button onClick={() => setCreateOpen(true)}>创建资源版本</Button>
            )}
          </div>
        </section>

        <section
          className={styles.panel}
          aria-labelledby="resource-versions-title"
        >
          <h2 id="resource-versions-title" className={styles.resourceName}>
            资源版本
          </h2>
          <p className={styles.subtitle}>
            主动升级或切回，不会改变任何已有任务。
          </p>
          {notice && (
            <p
              className={`${styles.notice} ${styles.successNotice}`}
              role="status"
            >
              {notice}
            </p>
          )}
          {error && (
            <p className={styles.error} role="alert">
              {sharedResourceErrorMessage(error)}
            </p>
          )}
          <div className={styles.versionList}>
            {versions.map((version) => {
              const current = version.version === currentVersion
              const action =
                version.version > (currentVersion ?? 0) ? '升级' : '切回'
              return (
                <div className={styles.versionRow} key={version.id}>
                  <div>
                    <div className={styles.versionName}>
                      v{version.version}
                      {current ? ' · 当前使用' : ''}
                    </div>
                    <div className={styles.versionMeta}>
                      {version.file_count} 个文件
                    </div>
                  </div>
                  <div className={styles.actions}>
                    {!current && (
                      <Button
                        variant="contained"
                        disabled={
                          !access.canEdit ||
                          stopped ||
                          !resource.current_selection ||
                          select.isPending
                        }
                        onClick={() => {
                          setNotice('')
                          select.mutate(version.version)
                        }}
                      >
                        {action}到 v{version.version}（仅新任务使用）
                      </Button>
                    )}
                    {resource.is_owner && access.canEdit && (
                      <Button
                        color="warning"
                        onClick={() => setDeleting(version)}
                      >
                        删除版本
                      </Button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </section>

        {currentVersion !== null && (
          <ResourceVersionDiff
            workspaceId={workspaceId}
            resourceId={resourceId}
            currentVersion={currentVersion}
            versions={versions}
          />
        )}
        <ResourceJobHistory
          workspaceId={workspaceId}
          resourceId={resourceId}
          resourceName={resource.name}
          currentVersion={currentVersion}
        />
      </>
    )
  }

  return (
    <AppShell
      appBar={({ scrolled }) => (
        <AppBar
          title={`${currentWorkspace?.name || workspaceId} / 管理版本`}
          backTo={`/workspaces/${workspaceId}/resources`}
          scrolled={scrolled}
        />
      )}
      mainClassName={styles.main}
    >
      <div className={styles.page}>
        <header>
          <h1 className={styles.title}>管理版本</h1>
          <p className={styles.subtitle}>
            升级、切回、查看文件差异和比较新旧任务。
          </p>
        </header>
        {body()}
      </div>
      {resource && createOpen && (
        <ResourceSourceDialog
          workspaceId={workspaceId}
          resource={resource}
          onClose={() => setCreateOpen(false)}
          onCreated={() =>
            setNotice('新版本已创建，选择后才会用于之后创建的任务。')
          }
        />
      )}
      {resource && shareOpen && (
        <ShareResourceDialog
          workspaceId={workspaceId}
          resource={resource}
          canEdit={access.canEdit}
          onClose={() => setShareOpen(false)}
        />
      )}
      {deleting && (
        <Dialog
          open
          onClose={remove.isPending ? undefined : () => setDeleting(null)}
        >
          <DialogTitle>删除 v{deleting.version}？</DialogTitle>
          <DialogContent>
            <p className={styles.help}>
              仍在被工作区或任务使用的版本会受到保护，无法删除。删除不会影响其他版本。
            </p>
            {remove.error && (
              <p className={styles.error} role="alert">
                {sharedResourceErrorMessage(remove.error)}
              </p>
            )}
          </DialogContent>
          <DialogActions>
            <Button
              onClick={() => setDeleting(null)}
              disabled={remove.isPending}
            >
              取消
            </Button>
            <Button
              color="warning"
              variant="contained"
              disabled={remove.isPending}
              onClick={() => remove.mutate(deleting.version)}
            >
              {remove.isPending ? '正在删除…' : '确认删除'}
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </AppShell>
  )
}
