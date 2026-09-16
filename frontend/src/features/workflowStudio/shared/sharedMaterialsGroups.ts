import type { SharedMaterialFileRow } from './sharedMaterialsRows'

export type SharedMaterialGroupKey = 'references' | 'scripts' | 'other'

export interface SharedMaterialGroup {
  key: SharedMaterialGroupKey
  label: string
  description: string
  rows: SharedMaterialFileRow[]
}

const GROUP_META: Record<SharedMaterialGroupKey, [string, string]> = {
  references: ['参考材料', '_shared/references/ 目录下的共享内容'],
  scripts: ['脚本', '_shared/scripts/ 目录下的共享内容'],
  other: ['其他', '_shared/ 根目录下的其他文件'],
}

function groupKeyOf(path: string): SharedMaterialGroupKey {
  const top = path.split('/', 1)[0]
  return top === 'references' || top === 'scripts' ? top : 'other'
}

/** Group the flat rows by top-level directory for the drawer (分组反馈):
 * references → scripts → 其他, empty groups omitted; in-group order
 * preserved (files already sorted, missing-source rows trail). */
export function groupSharedMaterialFileRows(
  rows: SharedMaterialFileRow[]
): SharedMaterialGroup[] {
  const order: SharedMaterialGroupKey[] = ['references', 'scripts', 'other']
  return order
    .map((key) => ({
      key,
      label: GROUP_META[key][0],
      description: GROUP_META[key][1],
      rows: rows.filter((row) => groupKeyOf(row.path) === key),
    }))
    .filter((group) => group.rows.length > 0)
}

/** Row label inside a group: the two material dirs are carried by the
 * group title, so their rows show the path WITHOUT the top-dir prefix;
 * 其他 rows keep the full relative path, and missing-source rows always
 * show the map source verbatim (that IS their identity). */
export function rowDisplayPath(
  groupKey: SharedMaterialGroupKey,
  row: SharedMaterialFileRow
): string {
  if (row.missingSource || groupKey === 'other') return row.path
  return row.path.slice(groupKey.length + 1)
}
