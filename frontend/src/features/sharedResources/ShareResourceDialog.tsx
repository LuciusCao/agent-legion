import { useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  listSharedResourceGrants,
  revokeSharedResource,
  shareSharedResource,
} from '../../api/sharedResources'
import { sharedResourceErrorMessage } from './sharedResourceError'
import { sharedResourceKeys } from './sharedResourceQueryKeys'
import { useEditableWorkspaceTargets } from './useWorkspaceAccess'
import type { SharedResource } from './sharedResourceTypes'
import styles from './SharedResources.module.css'

export function ShareResourceDialog({
  workspaceId,
  resource,
  canEdit,
  onClose,
}: {
  workspaceId: string
  resource: SharedResource
  canEdit: boolean
  onClose: () => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const targets = useEditableWorkspaceTargets(workspaceId)
  const [selectedId, setSelectedId] = useState('')
  const [receipt, setReceipt] = useState<{
    targetId: string
    targetName: string
    duplicate: boolean
  } | null>(null)
  const targetId = targets.workspaces.some((item) => item.id === selectedId)
    ? selectedId
    : (targets.workspaces[0]?.id ?? '')
  const target = targets.workspaces.find((item) => item.id === targetId)
  const grantsQuery = useQuery({
    queryKey: sharedResourceKeys.grants(workspaceId, resource.id),
    queryFn: ({ signal }) =>
      listSharedResourceGrants(workspaceId, resource.id, signal),
    enabled: resource.is_owner,
  })
  const share = useMutation({
    mutationFn: (id: string) =>
      shareSharedResource(workspaceId, resource.id, id),
    onSuccess: async (result, id) => {
      const targetName =
        targets.workspaces.find((item) => item.id === id)?.name ??
        result.grant.workspace_name
      setReceipt({ targetId: id, targetName, duplicate: result.duplicate })
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: sharedResourceKeys.list(id),
        }),
        queryClient.invalidateQueries({
          queryKey: sharedResourceKeys.grants(workspaceId, resource.id),
        }),
      ])
    },
  })
  const revoke = useMutation({
    mutationFn: (id: string) =>
      revokeSharedResource(workspaceId, resource.id, id),
    onSuccess: async (_result, id) => {
      setReceipt((current) => (current?.targetId === id ? null : current))
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: sharedResourceKeys.list(id),
        }),
        queryClient.invalidateQueries({
          queryKey: sharedResourceKeys.grants(workspaceId, resource.id),
        }),
      ])
    },
  })
  const error =
    share.error ?? revoke.error ?? targets.error ?? grantsQuery.error

  return (
    <Dialog
      open
      onClose={share.isPending ? undefined : onClose}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>共享「{resource.name}」</DialogTitle>
      <DialogContent>
        <p className={styles.help}>
          共享后，目标工作区会看到同一份资源，不会复制文件。
        </p>
        {!resource.is_owner ? (
          <p className={`${styles.notice} ${styles.warningNotice}`}>
            这个资源来自其他工作区。只有来源工作区可以管理共享。
          </p>
        ) : (
          <>
            {!canEdit && (
              <p className={`${styles.notice} ${styles.warningNotice}`}>
                你在来源工作区为只读成员，可以共享到你有编辑权限的其他工作区，不能撤销已有共享。
              </p>
            )}
            <label className={styles.field}>
              目标工作区
              <select
                className={styles.select}
                aria-label="目标工作区"
                value={targetId}
                onChange={(event) => {
                  setSelectedId(event.target.value)
                  setReceipt(null)
                  share.reset()
                }}
                disabled={targets.isLoading || share.isPending}
              >
                {targets.workspaces.length === 0 && (
                  <option value="">没有可共享的工作区</option>
                )}
                {targets.workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name}
                  </option>
                ))}
              </select>
            </label>
            <p className={styles.help}>只列出你可以编辑的其他工作区。</p>
            {target && (
              <div className={styles.actions}>
                <Button
                  variant="contained"
                  onClick={() => {
                    setReceipt(null)
                    share.mutate(target.id)
                  }}
                  disabled={share.isPending}
                >
                  {share.isPending ? '正在共享…' : `共享到${target.name}`}
                </Button>
              </div>
            )}
          </>
        )}
        {receipt && (
          <div
            className={`${styles.notice} ${styles.successNotice}`}
            role="status"
          >
            <p>
              {receipt.duplicate
                ? `此前已共享到${receipt.targetName}，无需重复操作。`
                : `已共享到${receipt.targetName}。`}
            </p>
            <Button
              onClick={() => {
                onClose()
                navigate(`/workspaces/${receipt.targetId}/resources`)
              }}
            >
              切换到{receipt.targetName}
            </Button>
          </div>
        )}
        {error && (
          <p className={styles.error} role="alert">
            {sharedResourceErrorMessage(error)}
          </p>
        )}
        {resource.is_owner && grantsQuery.data && (
          <div className={styles.grantList} aria-label="共享记录">
            {grantsQuery.data.grants
              .filter((grant) => grant.workspace_id !== workspaceId)
              .map((grant) => (
                <div className={styles.grantRow} key={grant.id}>
                  <div>
                    <div className={styles.grantName}>
                      {grant.workspace_name}
                    </div>
                    <div className={styles.grantMeta}>
                      {grant.status === 'active'
                        ? '可以查看并用于新任务'
                        : '已停止新任务使用，已有任务不受影响'}
                    </div>
                  </div>
                  {canEdit && grant.status === 'active' && (
                    <Button
                      color="warning"
                      onClick={() => revoke.mutate(grant.workspace_id)}
                      disabled={revoke.isPending}
                    >
                      停止新任务使用
                    </Button>
                  )}
                </div>
              ))}
          </div>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={share.isPending}>
          关闭
        </Button>
      </DialogActions>
    </Dialog>
  )
}
