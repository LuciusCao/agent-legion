import type { SkillFile } from '../../../types/agentCatalogTypes'

export type SkillDirNode = {
  name: string
  path: string
  dirs: SkillDirNode[]
  files: SkillFile[]
}

/** 把平铺的相对路径列表重建为目录树（根节点的 name/path 为空串）。 */
export function buildSkillFileTree(files: SkillFile[]): SkillDirNode {
  const root: SkillDirNode = { name: '', path: '', dirs: [], files: [] }
  for (const file of files) {
    const segments = file.path.split('/')
    let dir = root
    for (const segment of segments.slice(0, -1)) {
      const path = dir.path ? `${dir.path}/${segment}` : segment
      let next = dir.dirs.find((entry) => entry.name === segment)
      if (!next) {
        next = { name: segment, path, dirs: [], files: [] }
        dir.dirs.push(next)
      }
      dir = next
    }
    dir.files.push(file)
  }
  return root
}

export const skillFileName = (path: string) => path.split('/').pop() ?? path
