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

export interface JobFilterNodeOption {
  key: string
  label: string
}

export interface WorkflowVersionOptions {
  versionOptions: number[]
  hasMissingVersion: boolean
}
