import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { createRun } from '../api'
import { BULK_RUN_SUBMIT_THRESHOLD } from '../api/materialsApi'
import { useUiStore } from '../stores/uiStore'
import { useTaskCenterStore } from '../stores/taskCenterStore'
import { acceptRunItemsAsync } from './AddItemsDialogAsyncSubmit'
import { submitFailText, submitOkText } from './AddItemsDialogSubmitText'
import { textRunItem, type ResolvedTextItem } from '../lib/textItem'
import type { BundleEntry } from '../lib/bundleFolder'
import type { RunItem } from '../types'
import type { UploadEntry } from './useMaterialUploads'

type SubmitInputs = {
  workspaceId?: string
  workflowKey: string
  totalItems: number
  invalidText: boolean
  materialAccepted: boolean
  refAccepted: boolean
  bundleAccepted: boolean
  doneEntries: UploadEntry[]
  selectedMaterialIds: string[]
  readyBundles: BundleEntry[]
  refIds: string[]
  connectionKey: string
  textItems: number
  textItem: ResolvedTextItem
  resetState: () => void
  onClose: () => void
}

/**
 * 添加条目对话框的提交序列（#1157 phase3 从 AddItemsDialog 拆出，体积预算
 * 同 AddItemsDialogSubmitText 先例）：组装 RunItem 列表后——大批量
 * （>1000，BULK_RUN_SUBMIT_THRESHOLD）走异步受理臂（202 句柄 + 受理通知 +
 * 徽标 pulse），小批量保留同步快路径（M40 兜底文案不动）。
 */
export function useAddItemsSubmit({
  workspaceId,
  workflowKey,
  totalItems,
  invalidText,
  materialAccepted,
  refAccepted,
  bundleAccepted,
  doneEntries,
  selectedMaterialIds,
  readyBundles,
  refIds,
  connectionKey,
  textItems,
  textItem,
  resetState,
  onClose,
}: SubmitInputs) {
  const { showToast } = useUiStore()
  const showAcceptedNotice = useTaskCenterStore((s) => s.showAcceptedNotice)
  const queryClient = useQueryClient()
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = useCallback(async () => {
    if (!workspaceId || !workflowKey || totalItems === 0 || invalidText) return
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
      ...(textItems ? [textRunItem(textItem)] : []),
    ]
    setIsSubmitting(true)
    try {
      if (items.length > BULK_RUN_SUBMIT_THRESHOLD) {
        await acceptRunItemsAsync(
          workspaceId,
          items,
          queryClient,
          showAcceptedNotice
        )
      } else {
        const response = await createRun(workspaceId, { items })
        showToast(
          submitOkText(response.created_count, response.skipped_count),
          'success'
        )
      }
      resetState()
      onClose()
    } catch (err) {
      showToast(submitFailText(err, items.length), 'error')
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
    invalidText,
    textItem,
    showToast,
    showAcceptedNotice,
    queryClient,
    resetState,
    onClose,
  ])

  return { isSubmitting, handleSubmit }
}
