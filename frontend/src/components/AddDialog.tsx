import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { useUiStore } from '../stores/uiStore'
import { api, fetchPipelineDefinition } from '../api'
import { parseResourceInputs } from '../helpers'
import type {
  AddResult,
  ContentType,
  PipelineDefinitionRecord,
  PipelineIntakeModeRecord,
  VideoItem,
  WorkspaceRecord,
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
  context = 'video',
  workspaceId,
}: AddDialogProps) {
  const { addContentType, setAddContentType } = useUiStore()
  const [results, setResults] = useState<AddResult[]>([])
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [workspace, setWorkspace] = useState<WorkspaceRecord | null>(null)
  const [pipeline, setPipeline] = useState<PipelineDefinitionRecord | null>(
    null
  )
  const [selectedModeKey, setSelectedModeKey] = useState('')
  const [loadingModes, setLoadingModes] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const dialogRef = useRef<HTMLElement>(null)

  const modes = useMemo<PipelineIntakeModeRecord[]>(() => {
    if (!pipeline?.intake?.modes) return []
    const enabledModes = workspace?.intake_config?.enabled_modes || []
    if (enabledModes.length === 0) return pipeline.intake.modes
    const filtered = pipeline.intake.modes.filter((mode) =>
      enabledModes.includes(mode.key)
    )
    // If enabled_modes doesn't match any pipeline modes (e.g. after pipeline
    // switch), fall back to all pipeline modes rather than showing nothing.
    return filtered.length > 0 ? filtered : pipeline.intake.modes
  }, [pipeline, workspace])

  const getEffectiveLabel = useCallback(
    (mode: PipelineIntakeModeRecord): string => {
      const override = workspace?.intake_config?.label_overrides?.[mode.key]
      return override || mode.label
    },
    [workspace]
  )

  useEffect(() => {
    if (!open || context !== 'workspace' || !workspaceId) return
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoadingModes(true)
    let cancelled = false
    api<{ workspace: WorkspaceRecord }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}`
    )
      .then(({ workspace: ws }) => {
        if (cancelled) return
        setWorkspace(ws)
        const pipelineKey = ws.default_pipeline_key || 'question_content'
        return fetchPipelineDefinition(pipelineKey).then((result) => ({
          ws,
          result,
        }))
      })
      .then((data) => {
        if (cancelled || !data) return
        const { ws, result } = data
        setPipeline(result.pipeline)
        const availableModes = result.pipeline.intake?.modes || []
        const enabledModes = ws.intake_config?.enabled_modes || []
        const filtered =
          enabledModes.length === 0
            ? availableModes
            : availableModes.filter((mode) => enabledModes.includes(mode.key))
        setSelectedModeKey(filtered[0]?.key || '')
      })
      .finally(() => {
        if (!cancelled) setLoadingModes(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, context, workspaceId])

  const handleSubmit = useCallback(async () => {
    const input = textareaRef.current?.value || ''
    if (context === 'video') {
      const items = parseResourceInputs(input)
      if (items.length === 0) return
      setIsSubmitting(true)
      try {
        const response = await api<{
          videos: VideoItem[]
          results: AddResult[]
        }>('/api/videos', {
          method: 'POST',
          body: JSON.stringify({
            items: items.map((item) => ({
              content_type: addContentType,
              external_id: item.external_id,
              source_uuid: item.source_uuid,
            })),
          }),
        })
        setResults(response.results)
        if (textareaRef.current) textareaRef.current.value = ''
      } finally {
        setIsSubmitting(false)
      }
    } else {
      const values = input
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
      if (values.length === 0 || !workspaceId || !selectedModeKey) return
      const selectedMode = modes.find((m) => m.key === selectedModeKey)
      if (!selectedMode) return
      setIsSubmitting(true)
      try {
        const response = await api<{
          batch: Record<string, unknown>
          created_count: number
          jobs: Array<{ source_id: string; title: string; status: string }>
        }>(`/api/workspaces/${encodeURIComponent(workspaceId)}/job-batches`, {
          method: 'POST',
          body: JSON.stringify({
            pipeline_key: workspace?.default_pipeline_key || 'question_content',
            entity: workspace?.default_entity || 'question',
            source_kind: selectedMode.key,
            [selectedMode.input_field]: values,
            ...(selectedMode.input_field === 'question_ids'
              ? { knowledge_codes: [] }
              : { question_ids: [] }),
          }),
        })
        const mappedResults: AddResult[] = response.jobs.map((job) => ({
          external_id: job.source_id,
          content_type:
            (workspace?.default_entity as ContentType) || 'question',
          status: job.status || 'created',
          message: job.title,
        }))
        setResults(mappedResults)
        if (textareaRef.current) textareaRef.current.value = ''
      } finally {
        setIsSubmitting(false)
      }
    }
  }, [context, addContentType, workspaceId, selectedModeKey, workspace, modes])

  const handleClose = useCallback(() => {
    setResults([])
    setWorkspace(null)
    setPipeline(null)
    setSelectedModeKey('')
    onClose()
  }, [onClose])

  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    if (!dialog) return
    dialog.addEventListener('close', handleClose)
    dialog.addEventListener('closed', handleClose)
    return () => {
      dialog.removeEventListener('close', handleClose)
      dialog.removeEventListener('closed', handleClose)
    }
  }, [open, handleClose])

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
    <md-dialog
      ref={dialogRef}
      open
      style={
        {
          minWidth: '520px',
          '--md-dialog-container-color': '#ffffff',
        } as React.CSSProperties
      }
    >
      <div slot="headline">添加资源</div>
      <div slot="content">
        <div style={{ display: 'grid', gap: '16px', minWidth: '460px' }}>
          {isVideo && (
            <div style={{ display: 'flex', gap: '8px' }}>
              <md-outlined-button
                className={
                  addContentType === 'knowledge'
                    ? `${styles.typeBtn} ${styles.active}`
                    : styles.typeBtn
                }
                onClick={() => setAddContentType('knowledge')}
              >
                知识点
              </md-outlined-button>
              <md-outlined-button
                className={
                  addContentType === 'question'
                    ? `${styles.typeBtn} ${styles.active}`
                    : styles.typeBtn
                }
                onClick={() => setAddContentType('question')}
              >
                题目
              </md-outlined-button>
            </div>
          )}
          {!isVideo && (
            <md-outlined-select
              label="导入模式"
              value={selectedModeKey}
              onChange={(e: React.FormEvent<HTMLSelectElement>) =>
                setSelectedModeKey((e.target as HTMLSelectElement).value)
              }
            >
              {modes.map((mode) => (
                <md-select-option key={mode.key} value={mode.key}>
                  <div slot="headline">{getEffectiveLabel(mode)}</div>
                </md-select-option>
              ))}
            </md-outlined-select>
          )}
          <md-outlined-text-field
            ref={textareaRef}
            type="textarea"
            rows={8}
            label={textareaLabel}
            placeholder={placeholder}
          />
          {results.length > 0 && (
            <div className={styles.addResults}>
              {results.map((r, i) => (
                <div key={i} className={styles.addResult}>
                  <span>{r.external_id}</span>
                  <span>{r.status}</span>
                  <span>{r.message || ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div slot="actions">
        <md-text-button type="button" onClick={handleClose}>
          取消
        </md-text-button>
        <md-filled-button
          onClick={handleSubmit}
          disabled={isSubmitting || loadingModes || undefined}
        >
          {isSubmitting ? '处理中...' : '加入队列'}
        </md-filled-button>
      </div>
    </md-dialog>
  )
}
