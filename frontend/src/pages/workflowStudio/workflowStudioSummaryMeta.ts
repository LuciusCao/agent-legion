export type SummaryStatus = {
  label: string
  color: 'success' | 'warning' | 'error'
  filled: boolean
}

export function computeSummaryStatus(
  dirty: boolean,
  compareState: 'idle' | 'loading' | 'ready' | 'error',
  hasChanges: boolean,
  hasBreaking: boolean
): SummaryStatus {
  if (compareState === 'loading')
    return { label: '对比中', color: 'warning', filled: true }
  if (compareState === 'error')
    return { label: 'YAML 无法解析', color: 'error', filled: true }
  if (hasBreaking)
    return { label: '存在高风险变更', color: 'error', filled: true }
  if (hasChanges)
    return { label: '有未发布变更', color: 'warning', filled: true }
  return {
    label: dirty ? '有未保存修改' : '已同步',
    color: dirty ? 'warning' : 'success',
    filled: false,
  }
}

export {
  nodeChangeCounts,
  edgeChangeCounts,
} from './workflowStudioChangeCounts'
