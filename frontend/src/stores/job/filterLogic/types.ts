export type FilterDimension =
  | 'status'
  | 'workflowVersion'
  | 'activeNodeKey'
  | 'search'

export interface FilterCounts {
  status: Record<string, number>
  workflowVersion: Record<string, number>
  activeNodeKey: Record<string, number>
}
