import { groupValidationErrors } from './workflowStudioModel'

type Props = {
  message: string
  errors: string[]
}

export function WorkflowValidationPanel({ message, errors }: Props) {
  const groups = groupValidationErrors(errors)
  if (!message && errors.length === 0) return null
  return (
    <section aria-label="Workflow validation">
      {message && <p>{message}</p>}
      {groups.structural.length > 0 && (
        <>
          <h3>结构校验</h3>
          <ul>
            {groups.structural.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </>
      )}
      {groups.executor.length > 0 && (
        <>
          <h3>执行器绑定</h3>
          <ul>
            {groups.executor.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
