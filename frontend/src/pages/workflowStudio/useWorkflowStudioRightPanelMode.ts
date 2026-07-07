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
  const prevValidationRef = useRef({ message: '', errorsLength: 0 })

  useEffect(() => {
    const hasValidation =
      props.validationErrors.length > 0 || props.validationMessage !== ''
    const hadValidation =
      prevValidationRef.current.errorsLength > 0 ||
      prevValidationRef.current.message !== ''

    /* eslint-disable react-hooks/set-state-in-effect --
       Mode is derived from props and user actions; this effect only reconciles
       mode when validation appears/disappears or context (selection/forcedMode)
       changes, avoiding autoswitch loops while validation persists. */
    if (hasValidation && !hadValidation) {
      // Validation just appeared: autoswitch once, but remember any manual choice
      // so it can be restored when validation clears.
      setMode('validation')
    } else if (!hasValidation && hadValidation) {
      // Validation just cleared: restore context-driven or manual mode.
      if (props.forcedMode) {
        setMode(props.forcedMode)
      } else if (props.selectedNodeKey) {
        setMode('node')
      } else if (manualMode) {
        setMode(manualMode)
      } else {
        setMode('overview')
      }
    } else if (!hasValidation) {
      if (props.forcedMode) {
        setMode(props.forcedMode)
      } else if (props.selectedNodeKey) {
        setMode('node')
      } else if (!props.selectedNodeKey && mode === 'node') {
        setMode('overview')
      }
    }
    /* eslint-enable react-hooks/set-state-in-effect */

    prevValidationRef.current = {
      message: props.validationMessage,
      errorsLength: props.validationErrors.length,
    }
  }, [
    props.forcedMode,
    props.selectedNodeKey,
    props.validationErrors.length,
    props.validationMessage,
    mode,
    manualMode,
  ])

  const onTabChange = (value: PanelMode) => {
    setMode(value)
    setManualMode(value)
  }

  return { mode, onTabChange }
}
