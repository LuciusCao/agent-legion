import type { ValidationGroups } from '../shared/workflowStudioModel'

function categorizeValidationError(error: string): keyof ValidationGroups {
  const lower = error.toLowerCase()
  if (lower.includes('yaml') || lower.includes('parse')) return 'yaml'
  if (lower.includes('schema')) return 'schema'
  if (
    lower.includes('executor binding') ||
    lower.includes('not allocated') ||
    lower.includes('does not support capability')
  )
    return 'executor'
  if (lower.includes('revision') || lower.includes('active')) return 'revision'
  return 'structure'
}

export function groupValidationErrors(errors: string[]): ValidationGroups {
  return errors.reduce<ValidationGroups>(
    (groups, error) => {
      groups[categorizeValidationError(error)].push(error)
      return groups
    },
    { yaml: [], schema: [], structure: [], executor: [], revision: [] }
  )
}
