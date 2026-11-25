import type { MaterialRecord } from '../../types'
import type {
  MaterialBundleListResponse,
  SharedResourceSourceInput,
} from './sharedResourceTypes'

export type SourceOption = {
  key: string
  label: string
  source: SharedResourceSourceInput
}

export function sourceOptions(
  materials: MaterialRecord[],
  bundles: MaterialBundleListResponse['bundles']
): SourceOption[] {
  return [
    ...materials
      .filter((material) => material.status === 'ready')
      .map((material) => ({
        key: `material:${material.id}`,
        label: `材料 · ${material.filename}`,
        source: { type: 'material' as const, id: material.id },
      })),
    ...bundles.map((bundle) => ({
      key: `bundle:${bundle.id}`,
      label: `文件夹 · ${bundle.name}`,
      source: { type: 'bundle' as const, id: bundle.id },
    })),
  ]
}

export async function collectSourcePages<Item>(
  loadPage: (offset: number) => Promise<{ items: Item[]; total: number }>
): Promise<Item[]> {
  const collected: Item[] = []
  let offset = 0
  for (;;) {
    const page = await loadPage(offset)
    collected.push(...page.items)
    offset += page.items.length
    if (page.items.length === 0 || offset >= page.total) return collected
  }
}

export function newSubmissionKey(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `resource-${Date.now()}-${Math.random().toString(36).slice(2)}`
  )
}
