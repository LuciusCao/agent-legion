import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Tab,
  Tabs,
  TextField,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { api, createRun } from '../api'
import { useUiStore } from '../stores/uiStore'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { useWorkflowDefinitionQuery } from '../hooks/useWorkflowDefinitionQuery'
import { acceptedItemTypes } from '../lib/acceptedItemTypes'
import {
  fileTypeGroup,
  formatBytes,
  parseRefIds,
  uploadMaterialFile,
} from '../lib/addItems'
import type { RunItem, WorkspaceResponse } from '../types'
import { AddItemsExistingMaterials } from './AddItemsExistingMaterials'
import styles from './AddItemsDialog.module.css'

type UploadStatus = 'pending' | 'uploading' | 'done' | 'failed'

type UploadEntry = {
  key: string
  name: string
  size: number
  group: string
  status: UploadStatus
  error: string | null
  materialId: string | null
  deduplicated: boolean
}

type AddItemsDialogProps = {
  open: boolean
  onClose: () => void
  workspaceId?: string
}

const UPLOAD_CONCURRENCY = 4

const STATUS_LABELS: Record<UploadStatus, string> = {
  pending: '待传',
  uploading: '上传中',
  done: '完成',
  failed: '失败',
}

type TabKey = 'upload' | 'ref' | 'existing'

export function AddItemsDialog({
  open,
  onClose,
  workspaceId,
}: AddItemsDialogProps) {
  const { showToast } = useUiStore()
  const [tab, setTab] = useState<TabKey>('upload')
  const [entries, setEntries] = useState<UploadEntry[]>([])
  const [refText, setRefText] = useState('')
  const [connectionKey, setConnectionKey] = useState('')
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)

  const filesRef = useRef(new Map<string, { file: File; name: string }>())
  const queueRef = useRef<string[]>([])
  const activeRef = useRef(0)
  const keySeqRef = useRef(0)

  const enabled = open && Boolean(workspaceId)
  const workspaceQuery = useQuery({
    queryKey: extraQueryKeys.workspace(workspaceId ?? ''),
    queryFn: () =>
      api<WorkspaceResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId ?? '')}`
      ),
    enabled,
  })
  const workspace = workspaceQuery.data?.workspace ?? null
  const workflowKey = workspace?.default_workflow_key ?? ''

  // 入口契约：active revision 的 start 节点决定哪些条目类型可用
  // （EXEC-WORKFLOW-START-001）；取不到定义时缺省全接受。
  const workflowQuery = useWorkflowDefinitionQuery(open ? workspaceId : null)
  const acceptedTypes = acceptedItemTypes(workflowQuery.data)
  const materialAccepted = acceptedTypes.includes('material')
  const refAccepted = acceptedTypes.includes('ref')
  // 当前 tab 不被契约接受时落到可用 tab（派生值，不触发额外渲染循环）。
  const fallbackTab: TabKey = materialAccepted ? 'upload' : 'ref'
  const tabAllowed = tab === 'ref' ? refAccepted : materialAccepted
  const activeTab = tabAllowed ? tab : fallbackTab

  const updateEntry = useCallback(
    (key: string, patch: Partial<UploadEntry>) => {
      setEntries((prev) =>
        prev.map((entry) =>
          entry.key === key ? { ...entry, ...patch } : entry
        )
      )
    },
    []
  )

  const pumpRef = useRef<() => void>(() => {})
  const pump = useCallback(() => {
    while (activeRef.current < UPLOAD_CONCURRENCY && queueRef.current.length) {
      const key = queueRef.current.shift()!
      const record = filesRef.current.get(key)
      if (!record || !workspaceId) continue
      activeRef.current += 1
      updateEntry(key, { status: 'uploading', error: null })
      void uploadMaterialFile(workspaceId, record.file, record.name)
        .then((result) => {
          updateEntry(key, {
            status: 'done',
            materialId: result.materialId,
            deduplicated: result.deduplicated,
          })
        })
        .catch((err: unknown) => {
          updateEntry(key, {
            status: 'failed',
            error: err instanceof Error ? err.message : '上传失败',
          })
        })
        .finally(() => {
          activeRef.current -= 1
          pumpRef.current()
        })
    }
  }, [workspaceId, updateEntry])
  useEffect(() => {
    pumpRef.current = pump
  }, [pump])

  const addFiles = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return
      const next: UploadEntry[] = []
      for (const file of Array.from(fileList)) {
        const key = `f${++keySeqRef.current}`
        const name =
          (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
          file.name
        filesRef.current.set(key, { file, name })
        queueRef.current.push(key)
        next.push({
          key,
          name,
          size: file.size,
          group: fileTypeGroup(name, file.type),
          status: 'pending',
          error: null,
          materialId: null,
          deduplicated: false,
        })
      }
      setEntries((prev) => [...prev, ...next])
      pump()
    },
    [pump]
  )

  const retryEntry = useCallback(
    (key: string) => {
      updateEntry(key, { status: 'pending', error: null })
      queueRef.current.push(key)
      pump()
    },
    [pump, updateEntry]
  )

  const removeEntry = useCallback((key: string) => {
    filesRef.current.delete(key)
    setEntries((prev) => prev.filter((entry) => entry.key !== key))
  }, [])

  const toggleMaterial = useCallback((materialId: string) => {
    setSelectedMaterialIds((prev) =>
      prev.includes(materialId)
        ? prev.filter((id) => id !== materialId)
        : [...prev, materialId]
    )
  }, [])

  const resetState = useCallback(() => {
    setEntries([])
    setRefText('')
    setConnectionKey('')
    setSelectedMaterialIds([])
    setTab('upload')
    filesRef.current.clear()
    queueRef.current = []
  }, [])

  const refIds = useMemo(() => parseRefIds(refText), [refText])
  const doneEntries = useMemo(
    () =>
      entries.filter((entry) => entry.status === 'done' && entry.materialId),
    [entries]
  )
  const hasActiveUploads = entries.some(
    (entry) => entry.status === 'pending' || entry.status === 'uploading'
  )
  const totalItems =
    doneEntries.length + selectedMaterialIds.length + refIds.length
  const totalSize = useMemo(
    () => entries.reduce((sum, entry) => sum + entry.size, 0),
    [entries]
  )
  const groupCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const entry of entries) {
      counts.set(entry.group, (counts.get(entry.group) ?? 0) + 1)
    }
    return Array.from(counts.entries())
  }, [entries])

  const handleClose = useCallback(() => {
    resetState()
    onClose()
  }, [resetState, onClose])

  const handleSubmit = useCallback(async () => {
    if (!workspaceId || !workflowKey || totalItems === 0) return
    const items: RunItem[] = [
      ...doneEntries.map((entry) => ({
        type: 'material' as const,
        material_id: entry.materialId!,
      })),
      ...selectedMaterialIds.map((materialId) => ({
        type: 'material' as const,
        material_id: materialId,
      })),
      ...refIds.map((id) => ({
        type: 'ref' as const,
        connection_key: connectionKey.trim(),
        external_id: id,
      })),
    ]
    setIsSubmitting(true)
    try {
      const response = await createRun(workspaceId, {
        workflow_key: workflowKey,
        items,
      })
      showToast(`运行已创建，共 ${response.created_count} 个任务`, 'success')
      resetState()
      onClose()
    } catch (err) {
      const message = err instanceof Error ? err.message : '创建运行失败'
      showToast(`创建运行失败: ${message}`, 'error')
    } finally {
      setIsSubmitting(false)
    }
  }, [
    workspaceId,
    workflowKey,
    totalItems,
    doneEntries,
    selectedMaterialIds,
    refIds,
    connectionKey,
    showToast,
    resetState,
    onClose,
  ])

  if (!open) return null

  const submitDisabled =
    totalItems === 0 || isSubmitting || hasActiveUploads || !workflowKey

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { minWidth: '560px' } }}
    >
      <DialogTitle>添加条目</DialogTitle>
      <DialogContent>
        <div style={{ display: 'grid', gap: '12px', minWidth: '500px' }}>
          <Tabs
            value={activeTab}
            onChange={(_event, value: TabKey) => setTab(value)}
          >
            <Tab label="上传材料" value="upload" disabled={!materialAccepted} />
            <Tab label="粘贴 ID" value="ref" disabled={!refAccepted} />
            <Tab
              label="已有材料"
              value="existing"
              disabled={!materialAccepted}
            />
          </Tabs>
          {(!materialAccepted || !refAccepted) && (
            <div className={styles.errorHint} data-testid="item-type-hint">
              当前 workflow 只接受
              {materialAccepted ? '材料条目' : '外部引用条目'}
              （start 节点 accepted_item_types）。
            </div>
          )}
          {activeTab === 'upload' && (
            <>
              <div style={{ display: 'flex', gap: '8px' }}>
                <Button variant="outlined" component="label">
                  选择文件
                  <input
                    type="file"
                    multiple
                    hidden
                    data-testid="add-items-file-input"
                    onChange={(event) => {
                      addFiles(event.target.files)
                      event.target.value = ''
                    }}
                  />
                </Button>
                <Button variant="outlined" component="label">
                  选择文件夹
                  <input
                    type="file"
                    multiple
                    hidden
                    data-testid="add-items-folder-input"
                    {...{ webkitdirectory: '' }}
                    onChange={(event) => {
                      addFiles(event.target.files)
                      event.target.value = ''
                    }}
                  />
                </Button>
              </div>
              {entries.length > 0 && (
                <>
                  <div className={styles.summary} data-testid="upload-summary">
                    {groupCounts
                      .map(([group, count]) => `${group} × ${count}`)
                      .join('，')}
                    ，共 {formatBytes(totalSize)}
                  </div>
                  <div className={styles.fileList}>
                    {entries.map((entry) => (
                      <div className={styles.fileRow} key={entry.key}>
                        <span className={styles.fileName} title={entry.name}>
                          {entry.name}
                        </span>
                        <span className={styles.fileSize}>
                          {formatBytes(entry.size)}
                        </span>
                        <span
                          className={
                            entry.status === 'failed'
                              ? styles.statusFailed
                              : entry.status === 'done'
                                ? styles.statusDone
                                : styles.statusPending
                          }
                        >
                          {STATUS_LABELS[entry.status]}
                          {entry.deduplicated && entry.status === 'done'
                            ? '（已存在）'
                            : ''}
                        </span>
                        {entry.status === 'failed' && (
                          <Button
                            size="small"
                            onClick={() => retryEntry(entry.key)}
                          >
                            重试
                          </Button>
                        )}
                        {(entry.status === 'failed' ||
                          entry.status === 'pending') && (
                          <Button
                            size="small"
                            onClick={() => removeEntry(entry.key)}
                          >
                            移除
                          </Button>
                        )}
                      </div>
                    ))}
                    {entries.some((entry) => entry.status === 'failed') && (
                      <div className={styles.errorHint}>
                        失败文件不会包含在本次运行中，可重试或移除。
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          )}
          {activeTab === 'ref' && (
            <>
              <TextField
                label="连接 Key"
                placeholder="workflow 绑定的外部服务连接 key"
                value={connectionKey}
                onChange={(event) => setConnectionKey(event.target.value)}
                fullWidth
              />
              <TextField
                multiline
                rows={8}
                label="外部 ID"
                placeholder="一行一个 ID"
                value={refText}
                onChange={(event) => setRefText(event.target.value)}
                fullWidth
              />
              {refIds.length > 0 && (
                <div className={styles.summary} data-testid="ref-summary">
                  已解析 {refIds.length} 条引用
                </div>
              )}
            </>
          )}
          {activeTab === 'existing' && (
            <AddItemsExistingMaterials
              workspaceId={workspaceId}
              enabled={enabled}
              selectedIds={selectedMaterialIds}
              onToggle={toggleMaterial}
            />
          )}
          {!workflowKey && !workspaceQuery.isLoading && (
            <div className={styles.errorHint}>
              当前工作空间尚未发布 workflow，无法创建运行。
            </div>
          )}
        </div>
      </DialogContent>
      <DialogActions>
        <span className={styles.totalCount} data-testid="total-count">
          共 {totalItems} 个条目
        </span>
        <Button variant="text" onClick={handleClose}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={submitDisabled}
        >
          {isSubmitting ? '处理中...' : '创建运行'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
