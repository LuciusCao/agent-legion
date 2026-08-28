import errorStyles from './WorkflowValidationPanelErrors.module.css'
import groupStyles from './WorkflowValidationPanelGroups.module.css'

type Props = { title: string; errors: string[] }

export function WorkflowStringErrorGroup({ title, errors }: Props) {
  if (errors.length === 0) return null
  return (
    <div className={groupStyles.group}>
      <h3 className={groupStyles.groupTitle}>{title}</h3>
      <ul className={groupStyles.list}>
        {errors.map((error, index) => (
          <li key={`${title}-${index}`} className={errorStyles.listItem}>
            {error}
          </li>
        ))}
      </ul>
    </div>
  )
}
