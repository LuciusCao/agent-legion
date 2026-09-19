import { useCallback, useMemo, useState } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Tab,
  Tabs,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { api, createRun } from '../api'
import { useUiStore } from '../stores/uiStore'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { useWorkflowDefinitionQuery } from '../hooks/useWorkflowDefinitionQuery'
import { acceptedItemTypes } from '../lib/acceptedItemTypes'
import { parseRefIds } from '../lib/addItems'
import type { RunItem, WorkspaceResponse } from '../types'
import { AddItemsBundlePanel } from './AddItemsBundlePanel'
import { AddItemsContractHint } from './AddItemsContractHint'
import { AddItemsExistingMaterials } from './AddItemsExistingMaterials'
import { AddItemsRefPanel } from './AddItemsRefPanel'
import {
  AddItemsTextPanel,
  DEFAULT_TEXT_FILENAME,
  textItemReady,
  textRunItem,
} from './AddItemsTextPanel'
import { AddItemsUploadPanel } from './AddItemsUploadPanel'
import { useBundleUploads } from './useBundleUploads'
import { useMaterialUploads } from './useMaterialUploads'
import styles from './AddItemsDialog.module.css'

type AddItemsDialogProps = {
  open: boolean
  onClose: () => void
  workspaceId?: string
}

type TabKey = 'upload' | 'ref' | 'existing' | 'bundle' | 'text'

/**
 * 添加条目对话框：按条目类型各一个面板组件（上传材料 / 粘贴 ID /
 * 已有材料 / 文件夹打包 / 输入需求），可用的类型由 workflow start 节点的
 * 入口契约决定（EXEC-WORKFLOW-START-001）。
 */
export function AddItemsDialog({
  open,
  onClose,
  workspaceId,
}: AddItemsDialogProps) {
  const { showToast } = useUiStore()
  const [tab, setTab] = useState<TabKey>('upload')
  const [refText, setRefText] = useState('')
  const [connectionKey, setConnectionKey] = useState('')
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
  const [text, setText] = useState('')
  const [textFilename, setTextFilename] = useState(DEFAULT_TEXT_FILENAME)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const {
    doneEntries,
    hasActiveUploads,
    addFiles,
    retryEntry,
    removeEntry,
    resetUploads,
    entries,
  } = useMaterialUploads(workspaceId)
  const {
    bundles,
    readyBundles,
    hasActiveBundles,
    addFolder,
    retryBundle,
    removeBundle,
    resetBundles,
  } = useBundleUploads(workspaceId)

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
  const bundleAccepted = acceptedTypes.includes('bundle')
  const textAccepted = acceptedTypes.includes('text')
  const tabAllowedByKey: Record<TabKey, boolean> = {
    upload: materialAccepted,
    existing: materialAccepted,
    bundle: bundleAccepted,
    ref: refAccepted,
    text: textAccepted,
  }
  // 当前 tab 不被契约接受时落到首个可用 tab（派生值，不触发额外渲染循环）。
  const fallbackTab =
    (['upload', 'bundle', 'text', 'ref'] as const).find(
      (key) => tabAllowedByKey[key]
    ) ?? 'upload'
  const activeTab = tabAllowedByKey[tab] ? tab : fallbackTab

  const toggleMaterial = useCallback((materialId: string) => {
    setSelectedMaterialIds((prev) =>
      prev.includes(materialId)
        ? prev.filter((id) => id !== materialId)
        : [...prev, materialId]
    )
  }, [])

  const resetState = useCallback(() => {
    resetUploads()
    resetBundles()
    setRefText('')
    setConnectionKey('')
    setSelectedMaterialIds([])
    setText('')
    setTextFilename(DEFAULT_TEXT_FILENAME)
    setTab('upload')
  }, [resetUploads, resetBundles])

  const refIds = useMemo(() => parseRefIds(refText), [refText])
  // 一段需求文本 = 1 个条目；空白或超长不计数。
  const textItems = textAccepted && textItemReady(text) ? 1 : 0
  // 契约解析后收窄的窗口期：隐藏面板里残留的条目不计数、不提交。
  const totalItems =
    (materialAccepted ? doneEntries.length + selectedMaterialIds.length : 0) +
    (bundleAccepted ? readyBundles.length : 0) +
    (refAccepted ? refIds.length : 0) +
    textItems

  const handleClose = useCallback(() => {
    resetState()
    onClose()
  }, [resetState, onClose])

  const handleSubmit = useCallback(async () => {
    if (!workspaceId || !workflowKey || totalItems === 0) return
    const items: RunItem[] = [
      ...(materialAccepted ? doneEntries : []).map((entry) => ({
        type: 'material' as const,
        material_id: entry.materialId!,
      })),
      ...(materialAccepted ? selectedMaterialIds : []).map((materialId) => ({
        type: 'material' as const,
        material_id: materialId,
      })),
      ...(bundleAccepted ? readyBundles : []).map((bundle) => ({
        type: 'bundle' as const,
        bundle_id: bundle.bundleId!,
      })),
      ...(refAccepted ? refIds : []).map((id) => ({
        type: 'ref' as const,
        connection_key: connectionKey.trim(),
        external_id: id,
      })),
      ...(textItems ? [textRunItem(text, textFilename)] : []),
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
    materialAccepted,
    refAccepted,
    bundleAccepted,
    doneEntries,
    selectedMaterialIds,
    readyBundles,
    refIds,
    connectionKey,
    textItems,
    text,
    textFilename,
    showToast,
    resetState,
    onClose,
  ])

  if (!open) return null

  const submitDisabled =
    totalItems === 0 ||
    isSubmitting ||
    hasActiveUploads ||
    hasActiveBundles ||
    !workflowKey

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
            <Tab label="文件夹打包" value="bundle" disabled={!bundleAccepted} />
            <Tab label="输入需求" value="text" disabled={!textAccepted} />
          </Tabs>
          <AddItemsContractHint accepted={acceptedTypes} />
          {activeTab === 'upload' && (
            <AddItemsUploadPanel
              entries={entries}
              onAddFiles={addFiles}
              onRetry={retryEntry}
              onRemove={removeEntry}
            />
          )}
          {activeTab === 'ref' && (
            <AddItemsRefPanel
              connectionKey={connectionKey}
              refText={refText}
              onConnectionKeyChange={setConnectionKey}
              onRefTextChange={setRefText}
            />
          )}
          {activeTab === 'existing' && (
            <AddItemsExistingMaterials
              workspaceId={workspaceId}
              enabled={enabled}
              selectedIds={selectedMaterialIds}
              onToggle={toggleMaterial}
            />
          )}
          {activeTab === 'bundle' && (
            <AddItemsBundlePanel
              bundles={bundles}
              onAddFolder={addFolder}
              onRetry={retryBundle}
              onRemove={removeBundle}
            />
          )}
          {activeTab === 'text' && (
            <AddItemsTextPanel
              text={text}
              filename={textFilename}
              onTextChange={setText}
              onFilenameChange={setTextFilename}
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
