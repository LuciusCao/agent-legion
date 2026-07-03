import type { ChangeSummaryViewModel } from './workflowStudioChanges'

export function nodeChangeCounts(summary: ChangeSummaryViewModel | null) {
  if (!summary) return { added: 0, removed: 0, modified: 0 }
  return {
    added: summary.nodeChanges.filter((c) => c.type === 'added').length,
    removed: summary.nodeChanges.filter((c) => c.type === 'removed').length,
    modified: summary.nodeChanges.filter((c) => c.type === 'modified').length,
  }
}

export function edgeChangeCounts(summary: ChangeSummaryViewModel | null) {
  if (!summary) return { added: 0, removed: 0, changed: 0 }
  return {
    added: summary.edgeChanges.filter((c) => c.type === 'added').length,
    removed: summary.edgeChanges.filter((c) => c.type === 'removed').length,
    changed: summary.edgeChanges.filter(
      (c) => c.type === 'condition_changed' || c.type === 'label_changed'
    ).length,
  }
}

export function metadataChangeCounts(summary: ChangeSummaryViewModel | null) {
  if (!summary) return 0
  return summary.metadataChanges.length
}
