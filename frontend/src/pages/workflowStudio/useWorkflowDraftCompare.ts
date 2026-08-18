import { useEffect, useRef, useState } from 'react'
import { compareWorkflowDraft } from '../../api'
import {
  buildChangeSummary,
  ChangeSummaryViewModel,
} from './workflowStudioChanges'
import type {
  CompareError,
  CompareResponse,
  CompareState,
  UseWorkflowDraftCompareResult,
} from './useWorkflowDraftCompare.types'

export type { UseWorkflowDraftCompareResult } from './useWorkflowDraftCompare.types'

const DEBOUNCE_MS = 400

export function useWorkflowDraftCompare(
  workspaceId: string | undefined,
  definitionYaml: string,
  dirty: boolean,
  allowMissingBaseline: boolean = false
): UseWorkflowDraftCompareResult {
  const [compareState, setCompareState] = useState<CompareState>('idle')
  const [compareResponse, setCompareResponse] =
    useState<CompareResponse | null>(null)
  const [compareErrors, setCompareErrors] = useState<CompareError[] | null>(
    null
  )
  const [compareSummary, setCompareSummary] =
    useState<ChangeSummaryViewModel | null>(null)
  const requestCounter = useRef(0)
  const latestRequest = useRef(0)

  useEffect(() => {
    if (!workspaceId || !dirty) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reset is intentional when comparison is not applicable
      setCompareState('idle')
      setCompareResponse(null)
      setCompareErrors(null)
      setCompareSummary(null)
      return
    }

    const requestId = (requestCounter.current += 1)
    latestRequest.current = requestId
    const timer = setTimeout(async () => {
      setCompareState('loading')
      try {
        const response = await compareWorkflowDraft(workspaceId, {
          definition_yaml: definitionYaml,
          // 仅空态（从未发布）请求空基线预览；有 active revision 时该值无影响。
          allow_missing_baseline: allowMissingBaseline,
        })
        if (latestRequest.current !== requestId) return
        setCompareResponse(response)
        setCompareErrors(response.errors ?? null)
        setCompareSummary(buildChangeSummary(response))
        setCompareState('ready')
      } catch (error) {
        if (latestRequest.current !== requestId) return
        const message = error instanceof Error ? error.message : '对比请求失败'
        setCompareErrors([{ category: 'revision', message }])
        setCompareSummary(null)
        setCompareState('error')
      }
    }, DEBOUNCE_MS)

    return () => clearTimeout(timer)
  }, [workspaceId, definitionYaml, dirty, allowMissingBaseline])

  return { compareState, compareResponse, compareErrors, compareSummary }
}
