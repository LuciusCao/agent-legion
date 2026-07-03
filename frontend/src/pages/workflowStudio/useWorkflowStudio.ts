import { useEffect, useMemo, useState } from 'react'
import { publishWorkflowDraft, validateWorkflowDraft } from '../../api'
import { buildDagEdges, buildDagNodes } from './workflowStudioDag'
import { isDefinitionDirty } from './workflowStudioModel'
import { useWorkflowDraftCompare } from './useWorkflowDraftCompare'
import { useWorkflowStudioData } from './useWorkflowStudioData'

type ActionState = 'idle' | 'validating' | 'publishing'

export function useWorkflowStudio(workspaceId: string | undefined) {
  const [actionState, setActionState] = useState<ActionState>('idle')
  const [definitionYaml, setDefinitionYaml] = useState('')
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const [validationMessage, setValidationMessage] = useState('')
  const [reviewDialogOpen, setReviewDialogOpen] = useState(false)

  const { loadState, workflow, revision, revisions, originalYaml, reload } =
    useWorkflowStudioData(workspaceId)

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset draft to loaded original when it changes
    setDefinitionYaml(originalYaml)
  }, [originalYaml])

  const dirty = isDefinitionDirty(originalYaml, definitionYaml)
  const canSubmit = Boolean(workspaceId && definitionYaml.trim() && dirty)
  const nodes = useMemo(() => buildDagNodes(workflow), [workflow])
  const edges = useMemo(() => buildDagEdges(workflow), [workflow])

  const { compareState, compareErrors, compareSummary } =
    useWorkflowDraftCompare(workspaceId, definitionYaml, dirty)

  const hasCompareChanges = Boolean(
    compareSummary &&
    (compareSummary.nodeChanges.length > 0 ||
      compareSummary.edgeChanges.length > 0 ||
      compareSummary.intakeChanges.length > 0 ||
      compareSummary.riskFlags.length > 0)
  )
  const hasBlockingCompareError = Boolean(
    compareErrors &&
    compareErrors.length > 0 &&
    compareErrors.some(
      (error) => error.category === 'yaml' || error.category === 'schema'
    )
  )
  const canPublish =
    canSubmit &&
    compareState !== 'loading' &&
    !hasBlockingCompareError &&
    hasCompareChanges

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

  function requestPublish() {
    if (!canPublish) return
    setReviewDialogOpen(true)
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
    canPublish,
    validateDraft,
    publishDraft,
    requestPublish,
    resetDefinition: () => setDefinitionYaml(originalYaml),
    nodes,
    edges,
    compareState,
    compareErrors,
    compareSummary,
    reviewDialogOpen,
    closeReviewDialog: () => setReviewDialogOpen(false),
  }
}
