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

/** One file that rides in the same per-skill commit as the clicked row. */
export interface PropagateImpactFile {
  path: string
  /** true for the row the user clicked (the requested source). */
  requested: boolean
}

/**
 * Full impact scope of propagating one row (#683 review P2-1): the backend
 * treats ``sources`` as a SKILL selector — every selected skill syncs its
 * WHOLE mapped set in one commit + tag, so files mapped to those skills but
 * NOT to the clicked row ride along. Derivable entirely from the map data
 * already in the view (no extra API): the union of every source mapped to
 * at least one skill the clicked row maps to.
 */
export function collectPropagateImpact(
  row: SharedMaterialFileRow,
  rows: SharedMaterialFileRow[]
): { skills: string[]; files: PropagateImpactFile[] } {
  const skills = row.skills.map((entry) => entry.skill)
  const selected = new Set(skills)
  const paths = new Set<string>([row.path])
  for (const other of rows) {
    if (other.path === row.path) continue
    if (other.skills.some((entry) => selected.has(entry.skill))) {
      paths.add(other.path)
    }
  }
  return {
    skills,
    files: [...paths].sort().map((path) => ({
      path,
      requested: path === row.path,
    })),
  }
}
