import { useMemo, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from '@mui/material'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api'
import {
  createSharedResource,
  createSharedResourceVersion,
} from '../../api/sharedResources'
import type { MaterialListResponse } from '../../types'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { sharedResourceErrorMessage } from './sharedResourceError'
import { sharedResourceKeys } from './sharedResourceQueryKeys'
import {
  collectSourcePages,
  newSubmissionKey,
  sourceOptions,
} from './sharedResourceSource'
import type {
  MaterialBundleListResponse,
  SharedResource,
} from './sharedResourceTypes'
import styles from './SharedResources.module.css'

export function ResourceSourceDialog({
  workspaceId,
  resource,
  onClose,
  onCreated,
}: {
  workspaceId: string
  resource?: SharedResource
  onClose: () => void
  onCreated: () => void
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [selectedKey, setSelectedKey] = useState('')
  const [submissionKey, setSubmissionKey] = useState(newSubmissionKey)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const sourcesQuery = useQuery({
    queryKey: ['shared-resource-sources', workspaceId],
    queryFn: async ({ signal }) => {
      const path = `/api/workspaces/${encodeURIComponent(workspaceId)}`
      const [materials, bundles] = await Promise.all([
        collectSourcePages(async (offset) => {
          const page = await api<MaterialListResponse>(
            `${path}/materials?limit=200&offset=${offset}`,
            { signal }
          )
          return { items: page.materials, total: page.total }
        }),
        collectSourcePages(async (offset) => {
          const page = await api<MaterialBundleListResponse>(
            `${path}/material-bundles?limit=200&offset=${offset}`,
            { signal }
          )
          return { items: page.bundles, total: page.total }
        }),
      ])
      return { materials, bundles }
    },
  })
  const options = useMemo(
    () =>
      sourceOptions(
        sourcesQuery.data?.materials ?? [],
        sourcesQuery.data?.bundles ?? []
      ),
    [sourcesQuery.data]
  )
  const selected = options.find((option) => option.key === selectedKey)

  const submit = async () => {
    if (!selected || busy || (resource == null && !name.trim())) return
    setBusy(true)
    setError('')
    try {
      if (resource) {
        await createSharedResourceVersion(workspaceId, resource.id, {
          source: selected.source,
          idempotencyKey: submissionKey,
        })
        await queryClient.invalidateQueries({
          queryKey: sharedResourceKeys.versions(workspaceId, resource.id),
        })
      } else {
        await createSharedResource(workspaceId, {
          name: name.trim(),
          source: selected.source,
          idempotencyKey: submissionKey,
        })
        await queryClient.invalidateQueries({
          queryKey: sharedResourceKeys.list(workspaceId),
        })
        await queryClient.invalidateQueries({
          queryKey: extraQueryKeys.workspaceMaterials(workspaceId),
        })
      }
      onCreated()
      onClose()
    } catch (cause) {
      setError(
        sharedResourceErrorMessage(cause, '创建失败，请检查来源后重试。')
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
      <DialogTitle>{resource ? '创建资源版本' : '创建共享资源'}</DialogTitle>
      <DialogContent>
        <div className={styles.form}>
          {resource ? (
            <p className={styles.help}>
              为「{resource.name}
              」保存一个新版本。创建后可以选择何时用于新任务。
            </p>
          ) : (
            <label className={styles.field}>
              资源名称
              <input
                className={styles.input}
                value={name}
                onChange={(event) => {
                  setName(event.target.value)
                  setSubmissionKey(newSubmissionKey())
                }}
                maxLength={512}
                autoFocus
                placeholder="例如：通用审核规则"
              />
            </label>
          )}
          <label className={styles.field}>
            来源
            <select
              className={styles.select}
              aria-label="来源"
              value={selectedKey}
              onChange={(event) => {
                setSelectedKey(event.target.value)
                setSubmissionKey(newSubmissionKey())
              }}
              disabled={sourcesQuery.isLoading}
            >
              <option value="">
                {sourcesQuery.isLoading
                  ? '正在加载来源…'
                  : '请选择材料或文件夹'}
              </option>
              {options.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {sourcesQuery.isError && (
            <p className={styles.error} role="alert">
              来源列表加载失败，请关闭后重试。
            </p>
          )}
          {!sourcesQuery.isLoading &&
            !sourcesQuery.isError &&
            options.length === 0 && (
              <p className={styles.help}>
                当前没有可用材料或文件夹，请先在工作区中添加。
              </p>
            )}
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
        </div>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={() => void submit()}
          disabled={
            busy ||
            !selected ||
            sourcesQuery.isError ||
            (resource == null && !name.trim())
          }
        >
          {busy ? '正在创建…' : resource ? '创建新版本' : '创建资源'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
