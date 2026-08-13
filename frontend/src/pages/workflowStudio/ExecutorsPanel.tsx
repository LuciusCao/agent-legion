import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  TextField,
} from '@mui/material'
import { copyExecutor, fetchExecutorDefinitions } from '../../api'
import type { ExecutorListItem } from '../../types'
import { extraQueryKeys } from '../../lib/queryKeysExtra'
import { toErrorMessage } from '../../lib/queryError'
import { ExecutorEditor } from './ExecutorEditor'
import styles from './AgentsPanel.module.css'

const statusLabels: Record<ExecutorListItem['status'], string> = {
  draft: '草稿',
  published: '已发布',
  archived: '已归档',
}

/**
 * Executor 定义管理面板（Studio 全局对话框）：左侧列表 + 新建/复制，右侧
 * ExecutorEditor 负责草稿编辑、发布、归档与版本回滚。
 * 发布/回滚/归档写 DB 后调度 registry 热刷新，无需重启服务。
 */
export function ExecutorsPanel() {
  const queryClient = useQueryClient()
  const {
    data,
    isPending: loading,
    error: queryError,
  } = useQuery({
    queryKey: extraQueryKeys.executorDefinitions(),
    queryFn: fetchExecutorDefinitions,
  })
  const error = toErrorMessage(queryError)
  const executors = data?.executors ?? []
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [copySource, setCopySource] = useState<ExecutorListItem | null>(null)
  const [copyTarget, setCopyTarget] = useState('')
  const [copyError, setCopyError] = useState('')

  function refresh() {
    void queryClient.invalidateQueries({
      queryKey: extraQueryKeys.executorDefinitions(),
    })
  }

  function handleSelect(executorId: string) {
    setCreating(false)
    setSelectedId(executorId)
  }

  async function handleCopy() {
    const newExecutorId = copyTarget.trim()
    if (!copySource || !newExecutorId) return
    setCopyError('')
    try {
      await copyExecutor(copySource.executor_id, newExecutorId)
      setCopySource(null)
      setCopyTarget('')
      refresh()
      setCreating(false)
      setSelectedId(newExecutorId)
    } catch (err) {
      setCopyError(err instanceof Error ? err.message : '复制失败')
    }
  }

  return (
    <div>
      <p className={styles.hint} role="note">
        Executor 定义发布后调度立即热生效；列表与目录即时反映 DB 已发布内容。
      </p>
      <div className={styles.layout}>
        <div className={styles.list}>
          <div className={styles.listToolbar}>
            <Button
              size="small"
              variant="outlined"
              onClick={() => {
                setCreating(true)
                setSelectedId(null)
              }}
            >
              新建
            </Button>
          </div>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          {loading && <p className={styles.hint}>加载中...</p>}
          {!loading && executors.length === 0 && !error && (
            <p className={styles.empty}>暂无 Executor 定义</p>
          )}
          <ul className={styles.listItems}>
            {executors.map((executor) => (
              <li key={executor.executor_id}>
                <button
                  type="button"
                  className={
                    executor.executor_id === selectedId
                      ? styles.listItemActive
                      : styles.listItem
                  }
                  onClick={() => handleSelect(executor.executor_id)}
                >
                  <span className={styles.listItemTitle}>
                    {executor.executor_id}
                  </span>
                  <span className={styles.listItemMeta}>
                    <span>{executor.kind}</span>
                    <span>容量 {executor.global_capacity}</span>
                    <span>{executor.capabilities.length} 个 capability</span>
                    <span>{statusLabels[executor.status]}</span>
                    {executor.has_draft && <span>有草稿</span>}
                  </span>
                </button>
                <Button
                  size="small"
                  onClick={() => {
                    setCopySource(executor)
                    setCopyTarget('')
                    setCopyError('')
                  }}
                >
                  复制
                </Button>
              </li>
            ))}
          </ul>
        </div>
        <div className={styles.editor}>
          {creating || selectedId ? (
            <ExecutorEditor
              key={creating ? '__new__' : selectedId}
              executorId={creating ? null : selectedId}
              onSaved={(executorId) => {
                refresh()
                setCreating(false)
                setSelectedId(executorId)
              }}
              onChanged={refresh}
              onArchived={() => {
                refresh()
                setSelectedId(null)
              }}
            />
          ) : (
            <p className={styles.empty}>
              请选择左侧 Executor，或点击「新建」。
            </p>
          )}
        </div>

        <Dialog
          open={copySource !== null}
          onClose={() => setCopySource(null)}
          maxWidth="xs"
          fullWidth
        >
          <DialogTitle>复制 Executor「{copySource?.executor_id}」</DialogTitle>
          <DialogContent>
            <TextField
              label="新 Executor ID"
              variant="outlined"
              value={copyTarget}
              onChange={(e) => setCopyTarget(e.target.value)}
              fullWidth
              sx={{ mt: 1 }}
            />
            {copyError && (
              <p className={styles.error} role="alert">
                {copyError}
              </p>
            )}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setCopySource(null)}>取消</Button>
            <Button
              variant="contained"
              onClick={() => void handleCopy()}
              disabled={copyTarget.trim() === ''}
            >
              复制
            </Button>
          </DialogActions>
        </Dialog>
      </div>
    </div>
  )
}
