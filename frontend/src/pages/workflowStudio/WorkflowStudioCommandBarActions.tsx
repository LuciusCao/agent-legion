import { Button } from '@mui/material'

type Props = {
  readOnly: boolean
  dirty: boolean
  actionState: 'idle' | 'validating' | 'publishing'
  canSubmit: boolean
  canPublish: boolean
  onValidate: () => void
  onPublish: () => void
  onReset: () => void
  backToDraft: () => void
  useViewedRevisionAsDraft: () => void
}

type Action = [string, 'outlined' | 'contained', boolean, () => void]

export function WorkflowStudioCommandBarActions(props: Props) {
  const idle = props.actionState === 'idle'
  const actions: Action[] = props.readOnly
    ? [
        ['Back to draft', 'outlined', !idle, props.backToDraft],
        ['Use as draft', 'contained', !idle, props.useViewedRevisionAsDraft],
      ]
    : [
        ['校验', 'outlined', !props.canSubmit || !idle, props.onValidate],
        ['发布', 'contained', !props.canPublish || !idle, props.onPublish],
        ['重置', 'outlined', !props.dirty || !idle, props.onReset],
      ]
  return (
    <>
      {actions.map(([label, variant, disabled, onClick]) => (
        <Button
          key={label}
          size="small"
          variant={variant}
          disabled={disabled}
          onClick={onClick}
        >
          {label}
        </Button>
      ))}
    </>
  )
}
