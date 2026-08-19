import { useState, useCallback, useMemo } from 'react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  TextField,
} from '@mui/material'
import { useUiStore } from '../stores/uiStore'
import { api } from '../api'
import { useWorkflowDefinitionQuery } from '../hooks/useWorkflowDefinitionQuery'
import { useQuery } from '@tanstack/react-query'
import { extraQueryKeys } from '../lib/queryKeysExtra'
import type {
  WorkflowIntakeModeRecord,
  JobBatchResponse,
  WorkspaceResponse,
} from '../types'
import styles from './AddDialog.module.css'

type AddDialogProps = {
  open: boolean
  onClose: () => void
  context?: 'video' | 'workspace'
  workspaceId?: string
}

export function AddDialog({
  open,
  onClose,
  context = 'workspace',
  workspaceId,
}: AddDialogProps) {
  const { addContentType, setAddContentType, showToast } = useUiStore()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [selectedModeKeyOverride, setSelectedModeKey] = useState<string | null>(
    null
  )
  const [inputValue, setInputValue] = useState('')

  const intakeEnabled = open && context === 'workspace' && Boolean(workspaceId)
  const workspaceQuery = useQuery({
    queryKey: extraQueryKeys.workspace(workspaceId ?? ''),
    queryFn: () =>
      api<WorkspaceResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId ?? '')}`
      ),
    enabled: intakeEnabled,
  })
  const workspace = workspaceQuery.data?.workspace ?? null
  // active revision 定义直接按 workspaceId 取（schema v50）。
  const workflowQuery = useWorkflowDefinitionQuery(
    intakeEnabled ? workspaceId : undefined
  )
  const workflow = workflowQuery.data ?? null
  const loadingModes =
    intakeEnabled && (workspaceQuery.isLoading || workflowQuery.isLoading)

  const hasInput = inputValue.trim().length > 0

  const modes = useMemo<WorkflowIntakeModeRecord[]>(() => {
    if (!workflow?.intake?.modes) return []
    const rawEnabledModes = workspace?.intake_config?.enabled_modes
    if (rawEnabledModes === undefined) return workflow.intake.modes
    if (!Array.isArray(rawEnabledModes) || rawEnabledModes.length === 0)
      return []
    return workflow.intake.modes.filter((mode) =>
      rawEnabledModes.includes(mode.key)
    )
  }, [workflow, workspace])

  const selectedModeKey = selectedModeKeyOverride ?? modes[0]?.key ?? ''

  const submitDisabled =
    !hasInput ||
    isSubmitting ||
    loadingModes ||
    (context === 'workspace' && modes.length === 0)

  const getEffectiveLabel = useCallback(
    (mode: WorkflowIntakeModeRecord): string => {
      const labelOverrides = workspace?.intake_config?.label_overrides as
        | Record<string, string>
        | undefined
      const override = labelOverrides?.[mode.key]
      return override || mode.label
    },
    [workspace]
  )

  const handleSubmit = useCallback(async () => {
    const input = inputValue.trim()
    const reportError = (err: unknown, action: string) => {
      const message = err instanceof Error ? err.message : action
      showToast(`${action}失败: ${message}`, 'error')
    }
    if (context === 'video') return
    const values = input
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    if (values.length === 0 || !workspaceId || !selectedModeKey) return
    const selectedMode = modes.find((m) => m.key === selectedModeKey)
    if (!selectedMode) return
    setIsSubmitting(true)
    try {
      const response = await api<JobBatchResponse>(
        `/api/workspaces/${encodeURIComponent(workspaceId)}/job-batches`,
        {
          method: 'POST',
          body: JSON.stringify({
            workflow_key: workspace?.default_workflow_key,
            entity: workspace?.default_entity || 'question',
            source_kind: selectedMode.key,
            async_processing: true,
            [selectedMode.input_field]: values,
            ...(selectedMode.input_field === 'question_ids'
              ? { knowledge_codes: [] }
              : { question_ids: [] }),
          }),
        }
      )
      setInputValue('')
      const queued = ['queued', 'processing'].includes(
        String(response.batch.status)
      )
      showToast(
        queued
          ? `已加入队列，共 ${values.length} 项`
          : `批次已处理，共创建 ${response.created_count} 个任务`,
        'success'
      )
      onClose()
    } catch (err) {
      reportError(err, '创建任务')
    } finally {
      setIsSubmitting(false)
    }
  }, [
    context,
    workspaceId,
    selectedModeKey,
    workspace,
    modes,
    inputValue,
    showToast,
    onClose,
  ])

  const handleClose = useCallback(() => {
    setSelectedModeKey(null)
    setInputValue('')
    onClose()
  }, [onClose])

  if (!open) return null

  const isVideo = context === 'video'

  const placeholder = isVideo
    ? addContentType === 'knowledge'
      ? '一行一个知识点code，例如：x09010402\n或带source_uuid：x09010402,uuid-xxx'
      : '一行一个题目ID，例如：q12345678\n或带source_uuid：q12345678,uuid-xxx'
    : '一行一个 ID'

  const textareaLabel = isVideo
    ? `${addContentType === 'knowledge' ? '知识点' : '题目'} ID`
    : modes.find((m) => m.key === selectedModeKey)?.label || 'ID'

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth={false}
      PaperProps={{ sx: { minWidth: '520px' } }}
    >
      <DialogTitle>添加资源</DialogTitle>
      <DialogContent>
        <div style={{ display: 'grid', gap: '16px', minWidth: '460px' }}>
          {isVideo && (
            <div style={{ display: 'flex', gap: '8px' }}>
              <Button
                variant={
                  addContentType === 'knowledge' ? 'contained' : 'outlined'
                }
                className={styles.typeBtn}
                onClick={() => setAddContentType('knowledge')}
              >
                知识点
              </Button>
              <Button
                variant={
                  addContentType === 'question' ? 'contained' : 'outlined'
                }
                className={styles.typeBtn}
                onClick={() => setAddContentType('question')}
              >
                题目
              </Button>
            </div>
          )}
          {!isVideo && (
            <TextField
              select
              label="导入模式"
              value={selectedModeKey}
              onChange={(event) => setSelectedModeKey(event.target.value)}
              fullWidth
            >
              {modes.map((mode) => (
                <MenuItem key={mode.key} value={mode.key}>
                  {getEffectiveLabel(mode)}
                </MenuItem>
              ))}
            </TextField>
          )}
          {!isVideo && modes.length === 0 && !loadingModes && (
            <div className={styles.noModesHint}>
              当前工作空间未启用任何接入模式，请先在设置中配置并保存。
            </div>
          )}
          <TextField
            multiline
            rows={8}
            label={textareaLabel}
            placeholder={placeholder}
            value={inputValue}
            onChange={(event) => setInputValue(event.target.value)}
            fullWidth
          />
        </div>
      </DialogContent>
      <DialogActions>
        <Button variant="text" onClick={handleClose}>
          取消
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={submitDisabled}
        >
          {isSubmitting ? '处理中...' : '加入队列'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
