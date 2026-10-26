import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  cancelJobBatchOperation,
  pauseJobBatchOperation,
  resumeJobBatchOperation,
} from '../../api/jobBatchOperationControlApi'
import { useUiStore } from '../../stores/uiStore'
import { jobBatchOpKeys } from './useJobBatchOps'
import type { TaskCardModel } from './taskCardModel'
import type { TaskCardActions } from './TaskCardFooter'

type JobBatchOpControl = 'pause' | 'resume' | 'cancel'

const CONTROL_API = {
  pause: pauseJobBatchOperation,
  resume: resumeJobBatchOperation,
  cancel: cancelJobBatchOperation,
}

const CONTROL_LABEL = { pause: '暂停', resume: '继续', cancel: '取消' }
const CONTROL_SUCCESS = {
  pause: '已暂停。后续处理已挂起，已处理结果保留。',
  resume: '已继续处理。',
  cancel: '已取消。未处理部分已停止，已处理结果保留。',
}

function useJobBatchOpControl(
  workspaceId: string | undefined,
  action: JobBatchOpControl
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (opId: string) => CONTROL_API[action](workspaceId!, opId),
    onSuccess: () =>
      useUiStore.getState().showToast(CONTROL_SUCCESS[action], 'success'),
    onError: (error) => {
      const status = (error as { status?: number } | null)?.status
      const message =
        status === 409
          ? '操作状态已变化，已刷新最新进度。'
          : `${CONTROL_LABEL[action]}失败：${error instanceof Error ? error.message : '请重试'}`
      useUiStore.getState().showToast(message, 'error')
    },
    onSettled: () =>
      queryClient.invalidateQueries({
        queryKey: jobBatchOpKeys.all(workspaceId ?? ''),
      }),
  })
}

export function useJobBatchOpActions(workspaceId: string | undefined) {
  const pause = useJobBatchOpControl(workspaceId, 'pause')
  const resume = useJobBatchOpControl(workspaceId, 'resume')
  const cancel = useJobBatchOpControl(workspaceId, 'cancel')
  return { pause, resume, cancel }
}

export type JobBatchOpPendingControl = {
  action: 'pause' | 'cancel'
  card: TaskCardModel
}

export function useJobBatchOpCardActions(workspaceId: string | undefined) {
  const opActions = useJobBatchOpActions(workspaceId)
  const [pendingControl, setPendingControl] =
    useState<JobBatchOpPendingControl | null>(null)

  const actionsFor = (card: TaskCardModel): TaskCardActions => ({
    onPause: () => {
      if (card.status === 'running')
        setPendingControl({ action: 'pause', card })
    },
    onResume: (id) => {
      if (card.status === 'paused') opActions.resume.mutate(id)
    },
    onCancel: () => {
      if (['running', 'paused', 'queued'].includes(card.status))
        setPendingControl({ action: 'cancel', card })
    },
  })

  return {
    pendingControl,
    closeControl: () => setPendingControl(null),
    actionsFor,
  }
}
