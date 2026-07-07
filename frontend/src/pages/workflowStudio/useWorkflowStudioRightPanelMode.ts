import { useEffect, useRef, useState } from 'react'

type PanelMode = 'overview' | 'node' | 'changes' | 'yaml' | 'validation'

type UsePanelModeProps = {
  selectedNodeKey: string | null
  forcedMode?: 'changes' | 'yaml'
  validationMessage: string
  validationErrors: string[]
}
export function useWorkflowStudioRightPanelMode(props: UsePanelModeProps) {
  const [mode, setMode] = useState<PanelMode>('overview')
  const [manualMode, setManualMode] = useState<PanelMode | null>(null)
  const prevRef = useRef({
    selectedNodeKey: null as string | null,
    forcedMode: undefined as 'changes' | 'yaml' | undefined,
    validationMessage: '',
    validationErrorsLength: 0,
  })
  useEffect(() => {
    const prev = prevRef.current
    const hasValidation =
      props.validationErrors.length > 0 || props.validationMessage !== ''
    const hadValidation =
      prev.validationErrorsLength > 0 || prev.validationMessage !== ''

    /* eslint-disable react-hooks/set-state-in-effect -- derived state reconciliation */
    let nextMode: PanelMode | null = null
    if (hasValidation && !hadValidation) {
      nextMode = 'validation'
    } else if (!hasValidation && hadValidation) {
      if (props.forcedMode) nextMode = props.forcedMode
      else if (props.selectedNodeKey) nextMode = 'node'
      else if (manualMode && manualMode !== 'node') nextMode = manualMode
      else nextMode = 'overview'
    } else if (!hasValidation) {
      if (prev.forcedMode != null && props.forcedMode == null) {
        if (manualMode === prev.forcedMode) setManualMode(null)
        nextMode = props.selectedNodeKey ? 'node' : 'overview'
      }
      // prettier-ignore
      if (nextMode === null && props.forcedMode && props.forcedMode !== prev.forcedMode) {
        nextMode = props.forcedMode
      }
      // prettier-ignore
      if (nextMode === null && props.selectedNodeKey && props.selectedNodeKey !== prev.selectedNodeKey) {
        nextMode = 'node'
      }
      // prettier-ignore
      if (nextMode === null && prev.selectedNodeKey != null && props.selectedNodeKey == null && !props.forcedMode && (!manualMode || manualMode === 'node')) {
        nextMode = 'overview'
      }
    }

    if (manualMode === 'node' && !props.selectedNodeKey) setManualMode(null)
    if (nextMode !== null) setMode(nextMode)
    /* eslint-enable react-hooks/set-state-in-effect */

    // prettier-ignore
    prevRef.current = { selectedNodeKey: props.selectedNodeKey, forcedMode: props.forcedMode, validationMessage: props.validationMessage, validationErrorsLength: props.validationErrors.length }
  }, [
    props.forcedMode,
    props.selectedNodeKey,
    props.validationErrors.length,
    props.validationMessage,
    manualMode,
  ])

  const onTabChange = (value: PanelMode) => {
    setMode(value)
    setManualMode(value)
  }

  return { mode, onTabChange }
}
