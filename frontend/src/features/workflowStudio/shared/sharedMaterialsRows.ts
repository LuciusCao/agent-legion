import type {
  SharedMaterialSkillDrift,
  WorkspaceSharedMaterialsResponse,
} from '../../../api'

/**
 * One row of the merged shared-materials file list (issue #643 重设计):
 * the file's own metadata plus the drift badges of the skills its map
 * entry targets. Files no map entry references carry an empty ``skills``
 * (rendered as an 未映射 chip); map sources missing from ``_shared`` get a
 * synthetic trailing row with ``missingSource`` (rendered as a 缺失源 row
 * — its per-skill badges still show, the propagate action stays off
 * because the shared source itself is unreadable).
 */
export interface SharedMaterialFileRow {
  path: string
  size: number | null
  modifiedAt: string | null
  skills: SharedMaterialSkillDrift[]
  missingSource: boolean
}

export function buildSharedMaterialFileRows(
  data: WorkspaceSharedMaterialsResponse
): SharedMaterialFileRow[] {
  const materials = data.map?.materials ?? []
  const skillsBySource = new Map(materials.map((m) => [m.source, m.skills]))
  const rows: SharedMaterialFileRow[] = (data.files ?? []).map((file) => ({
    path: file.path,
    size: file.size,
    modifiedAt: file.modified_at,
    skills: skillsBySource.get(file.path) ?? [],
    missingSource: false,
  }))
  const listed = new Set(rows.map((row) => row.path))
  for (const material of materials) {
    if (listed.has(material.source)) continue
    rows.push({
      path: material.source,
      size: null,
      modifiedAt: null,
      skills: material.skills,
      missingSource: true,
    })
  }
  return rows
}

/** Propagate is offered only when at least one mapped skill is behind
 * (pending_sync) or lacks the file (missing_in_skill) — a fully synced
 * row would no-op, and a missing source row would fail the save. */
export function isRowPropagatable(row: SharedMaterialFileRow): boolean {
  return (
    !row.missingSource &&
    row.skills.some(
      (entry) =>
        entry.status === 'pending_sync' || entry.status === 'missing_in_skill'
    )
  )
}
