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
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, createRun } from '../api'
import { useUiStore } from '../stores/uiStore'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import { queryKeys } from '../lib/queryKeys'
import { useWorkflowDefinitionQuery } from '../hooks/useWorkflowDefinitionQuery'
import { acceptedItemTypes, itemTypeLabel } from '../lib/acceptedItemTypes'
import { parseRefIds, readFileText } from '../lib/addItems'
import {
  createCampaignFromManifest,
  createSubmitCampaign,
} from '../api/campaignApi'
import type { RunItem, WorkspaceResponse } from '../types'
import type { CampaignSubmitInlineTarget } from '../types/campaignTypes'
import { AddItemsBundlePanel } from './AddItemsBundlePanel'
import { AddItemsExistingMaterials } from './AddItemsExistingMaterials'
import { AddItemsRefPanel } from './AddItemsRefPanel'
import { AddItemsUploadPanel } from './AddItemsUploadPanel'
import { useBundleUploads } from './useBundleUploads'
import { useMaterialUploads } from './useMaterialUploads'
import styles from './AddItemsDialog.module.css'

type AddItemsDialogProps = {
  open: boolean
  onClose: () => void
  workspaceId?: string
}

type TabKey = 'upload' | 'ref' | 'existing' | 'bundle'

/**
 * 「添加条目」对话框：按条目类型各一个面板组件（上传材料 / 粘贴 ID /
 * 已有材料 / 文件夹打包），可用的类型由 workflow start 节点的入口契约决定
 * （EXEC-WORKFLOW-START-001）。
 *
 * 批量任务化（#532 PR-D 定稿）：「粘贴 ID」与文件清单通道不论多少条都
 * 创建批量任务（submit 模式）——用户对分批无感，任务按执行节奏自动分批
 * 创建，进度在「批量任务」页可见（旧版 2,000 条分界取消）。材料上传 /
 * 已有材料 / 文件夹打包保持原「直接创建运行」路径（各自的多文件流程
 * 不变）；两类条目混填时统一走批量任务（清单含全部条目，去重语义相同）。
 */
export function AddItemsDialog({
  open,
  onClose,
  workspaceId,
}: AddItemsDialogProps) {
  const { showToast } = useUiStore()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<TabKey>('upload')
  const [refText, setRefText] = useState('')
  const [connectionKey, setConnectionKey] = useState('')
  const [manifest, setManifest] = useState<{
    file: File
    /** 规整后的 jsonl 清单（ref 条目补 connection_key；对象行透传）。 */
    payload: File
    count: number
  } | null>(null)
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([])
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
  // 当前 tab 不被契约接受时落到可用 tab（派生值，不触发额外渲染循环）。
  const fallbackTab: TabKey = materialAccepted
    ? 'upload'
    : bundleAccepted
      ? 'bundle'
      : 'ref'
  const tabAllowed =
    tab === 'ref'
      ? refAccepted
      : tab === 'bundle'
        ? bundleAccepted
        : materialAccepted
  const activeTab = tabAllowed ? tab : fallbackTab

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
    setManifest(null)
    setSelectedMaterialIds([])
    setTab('upload')
  }, [resetUploads, resetBundles])

  const refIds = useMemo(() => parseRefIds(refText), [refText])
  // 契约解析后收窄的窗口期：隐藏面板里残留的条目不计数、不提交。
  const totalItems =
    (materialAccepted ? doneEntries.length + selectedMaterialIds.length : 0) +
    (bundleAccepted ? readyBundles.length : 0) +
    (refAccepted ? refIds.length + (manifest?.count ?? 0) : 0)

  // 走批量任务的判定：粘贴 ID 或清单文件有条目（ref 通道）——材料类条目
  // 与之混填时一并进清单；纯材料提交维持直接创建运行。
  const usesCampaign = refAccepted && (refIds.length > 0 || manifest != null)

  const handleClose = useCallback(() => {
    resetState()
    onClose()
  }, [resetState, onClose])

  /** 文件清单 → 规整后的 jsonl（ref 条目补 connection_key；对象行透传）。 */
  const normalizeManifest = useCallback(
    async (file: File, key: string): Promise<File | null> => {
      const text = await readFileText(file)
      const lines: string[] = []
      for (const raw of text.split('\n')) {
        const line = raw.trim()
        if (!line || line.startsWith('#')) continue
        if (line.startsWith('{')) {
          lines.push(line)
          continue
        }
        // 裸 ID 行：csv 多列时取首列。
        const externalId = line.split(',')[0]?.trim() ?? ''
        if (!externalId) continue
        lines.push(
          JSON.stringify({
            type: 'ref',
            connection_key: key.trim(),
            external_id: externalId,
          })
        )
      }
      if (lines.length === 0) return null
      return new File([lines.join('\n') + '\n'], 'manifest.jsonl', {
        type: 'application/x-ndjson',
      })
    },
    []
  )

  const handleManifestPicked = useCallback(
    async (file: File | null) => {
      if (!file) {
        setManifest(null)
        return
      }
      const payload = await normalizeManifest(file, connectionKey)
      if (!payload) {
        showToast(`${file.name} 中没有可用条目`, 'error')
        setManifest(null)
        return
      }
      const count = (await readFileText(payload))
        .split('\n')
        .filter((line) => line.trim() !== '').length
      setManifest({ file, payload, count })
    },
    [normalizeManifest, connectionKey, showToast]
  )

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
    ]
    setIsSubmitting(true)
    try {
      if (usesCampaign) {
        // 批量任务通道（#532 定稿）：清单文件走 multipart（服务端规整），
        // 粘贴走行内清单——两者对用户都是「添加后自动分批创建」。
        // 审核 P1：清单文件与粘贴 ID / 材料条目并存时必须合并进同一
        // multipart 清单（服务端 normalize_item 收 material/bundle/ref
        // 三型）——旧代码只上传文件 payload，同时存在的其他条目被静默
        // 丢弃而 toast 按合并口径报成功。
        if (manifest && items.length > 0) {
          const manifestLines = (await readFileText(manifest.payload))
            .split('\n')
            .filter((line) => line.trim() !== '')
          const extraLines = items.map((item) => JSON.stringify(item))
          const merged = new File(
            [[...manifestLines, ...extraLines].join('\n') + '\n'],
            'manifest.jsonl',
            { type: 'application/x-ndjson' }
          )
          await createCampaignFromManifest(workspaceId, merged)
        } else if (manifest) {
          await createCampaignFromManifest(workspaceId, manifest.payload)
        } else {
          const submit: CampaignSubmitInlineTarget = { items }
          await createSubmitCampaign(workspaceId, submit)
        }
        showToast(
          `批量任务已创建，共 ${totalItems} 个条目将按执行节奏自动创建任务，进度可在「批量任务」页查看`,
          'success'
        )
        void queryClient.invalidateQueries({
          queryKey: queryKeys.campaigns(workspaceId),
        })
        resetState()
        onClose()
        return
      }
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
    usesCampaign,
    manifest,
    showToast,
    queryClient,
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
          </Tabs>
          {(!materialAccepted || !refAccepted || !bundleAccepted) && (
            <div className={styles.errorHint} data-testid="item-type-hint">
              当前工作流只接受：
              {
                // 规范顺序 material/ref/bundle：逐布尔展开（而不是 filter
                // acceptedTypes）让顺序与契约常量 ACCEPTED_ITEM_TYPES 解耦，
                // 改数组顺序不会意外改变展示顺序。
                [
                  materialAccepted && itemTypeLabel('material'),
                  refAccepted && itemTypeLabel('ref'),
                  bundleAccepted && itemTypeLabel('bundle'),
                ]
                  .filter(Boolean)
                  .join('、')
              }
              。其他提交方式已隐藏，可在 Studio 的入口节点调整。
            </div>
          )}
          {activeTab === 'upload' && (
            <AddItemsUploadPanel
              entries={entries}
              onAddFiles={addFiles}
              onRetry={retryEntry}
              onRemove={removeEntry}
            />
          )}
          {activeTab === 'ref' && (
            <>
              <AddItemsRefPanel
                connectionKey={connectionKey}
                refText={refText}
                onConnectionKeyChange={setConnectionKey}
                onRefTextChange={setRefText}
              />
              <Button variant="outlined" component="label" size="small">
                或上传清单文件（.csv / .jsonl / .txt，一行一个 ID）
                <input
                  type="file"
                  hidden
                  accept=".csv,.jsonl,.txt"
                  data-testid="add-items-manifest-input"
                  onChange={(event) => {
                    void handleManifestPicked(event.target.files?.[0] ?? null)
                    event.target.value = ''
                  }}
                />
              </Button>
              {manifest && (
                <div className={styles.summary} data-testid="manifest-summary">
                  已选择 {manifest.file.name}（解析 {manifest.count} 条，与
                  粘贴的 ID 一并提交）
                </div>
              )}
              <div className={styles.summary}>
                任务会按执行节奏自动分批创建，进度在「批量任务」页可见
              </div>
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
          {activeTab === 'bundle' && (
            <AddItemsBundlePanel
              bundles={bundles}
              onAddFolder={addFolder}
              onRetry={retryBundle}
              onRemove={removeBundle}
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
          {isSubmitting ? '处理中...' : usesCampaign ? '添加' : '创建运行'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
