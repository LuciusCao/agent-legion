import type { WorkflowRevisionSummary } from '../../types'

type Props = {
  revisions: WorkflowRevisionSummary[]
  activeRevisionId?: string
}

export function WorkflowRevisionList({ revisions, activeRevisionId }: Props) {
  return (
    <section aria-label="Workflow revisions">
      <h2>版本</h2>
      {revisions.length === 0 ? (
        <p>当前 workspace 还没有 workflow revision</p>
      ) : (
        <ul>
          {revisions.map((revision) => (
            <li
              key={revision.id}
              aria-current={
                revision.id === activeRevisionId ? 'true' : undefined
              }
            >
              <span>v{revision.version}</span>
              <span>{revision.status}</span>
              <span>{revision.definition_hash.slice(0, 8)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
