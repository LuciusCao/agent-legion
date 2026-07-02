import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchActiveWorkflowRevision,
  fetchWorkflowRevisions,
  publishWorkflowDraft,
  validateWorkflowDraft,
} from '../../api'
import type {
  WorkflowDefinitionRecord,
  WorkflowRevisionSummary,
} from '../../types'
import { buildDagEdges, buildDagNodes } from './workflowStudioDag'
import { isDefinitionDirty } from './workflowStudioModel'

type LoadState = 'loading' | 'ready' | 'empty' | 'error'
type ActionState = 'idle' | 'validating' | 'publishing'
type StudioData = {
  workflow: WorkflowDefinitionRecord | null
  revision: WorkflowRevisionSummary | null
  revisions: WorkflowRevisionSummary[]
  definition_yaml: string
}

export function useWorkflowStudio(workspaceId: string | undefined) {
  const [loadState, setLoadState] = useState<LoadState>('loading')
  const [actionState, setActionState] = useState<ActionState>('idle')
  const [workflow, setWorkflow] = useState<WorkflowDefinitionRecord | null>(
    null
  )
  const [revision, setRevision] = useState<WorkflowRevisionSummary | null>(null)
  const [revisions, setRevisions] = useState<WorkflowRevisionSummary[]>([])
  const [originalYaml, setOriginalYaml] = useState('')
  const [definitionYaml, setDefinitionYaml] = useState('')
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [validationMessage, setValidationMessage] = useState('')

  const applyData = useCallback((data: StudioData) => {
    setWorkflow(data.workflow)
    setRevision(data.revision)
    setRevisions(data.revisions)
    setOriginalYaml(data.definition_yaml)
    setDefinitionYaml(data.definition_yaml)
    setValidationErrors([])
    setValidationMessage('')
  }, [])

  const reload = useCallback(async () => {
    if (!workspaceId) return
    setLoadState('loading')
    try {
      const active = await fetchActiveWorkflowRevision(workspaceId)
      const history = await fetchWorkflowRevisions(workspaceId)
      applyData({ ...active, revisions: history.revisions })
      setLoadState('ready')
    } catch {
      setLoadState('error')
    }
  }, [applyData, workspaceId])

  useEffect(() => {
    if (!workspaceId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoadState('empty')
      return
    }
    void reload()
  }, [reload, workspaceId])

  const dirty = isDefinitionDirty(originalYaml, definitionYaml)
  const canSubmit = Boolean(workspaceId && definitionYaml.trim())
  const nodes = useMemo(() => buildDagNodes(workflow), [workflow])
  const edges = useMemo(() => buildDagEdges(workflow), [workflow])

  async function validateDraft() {
    if (!workspaceId) return
    setActionState('validating')
    try {
      const result = await validateWorkflowDraft(workspaceId, definitionYaml)
      setValidationErrors(result.errors)
      setValidationMessage(result.valid ? '校验通过' : '校验失败')
    } finally {
      setActionState('idle')
    }
  }

  async function publishDraft() {
    if (!workspaceId) return
    setActionState('publishing')
    try {
      const result = await publishWorkflowDraft(workspaceId, definitionYaml)
      setValidationErrors(result.errors)
      if (result.valid) {
        await reload()
        setValidationMessage('发布成功')
      } else {
        setValidationMessage('发布失败')
      }
    } finally {
      setActionState('idle')
    }
  }

  return {
    loadState,
    actionState,
    workflow,
    revision,
    revisions,
    definitionYaml,
    setDefinitionYaml,
    selectedNodeKey,
    setSelectedNodeKey,
    validationErrors,
    validationMessage,
    dirty,
    canSubmit,
    validateDraft,
    publishDraft,
    resetDefinition: () => setDefinitionYaml(originalYaml),
    nodes,
    edges,
  }
}
