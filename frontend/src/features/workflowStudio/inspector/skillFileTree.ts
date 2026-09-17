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

/** skill 根目录核心文件的置顶优先级表（issue #676）：SKILL.md（指令
 * 本体）与 contract.yaml（机器可读契约）固定排在文件列表最前，二者
 * 之间 SKILL.md 在前。新增核心文件类型只需在表尾追加，渲染层与查询
 * 函数自动跟进。 */
export const SKILL_CORE_FILE_ORDER: readonly string[] = [
  'SKILL.md',
  'contract.yaml',
]

/** 核心文件置顶序号（优先级表查询）：命中表返回表内下标（0 起），
 * 未命中返回表长（排在全部核心文件之后，由调用方继续按 localeCompare
 * 排序）。 */
export function skillCoreFileRank(path: string): number {
  const rank = SKILL_CORE_FILE_ORDER.indexOf(path)
  return rank === -1 ? SKILL_CORE_FILE_ORDER.length : rank
}

/** 分拣目录节点内的文件（issue #676）：置顶只在根目录（isRoot）生效
 * ——核心文件总在 skill 根——core 按优先级表序返回，供渲染层排在
 * 子目录之前；其余文件（含子目录内的一切文件，同名也不置顶）与
 * isRoot=false 时的全部文件都按 path localeCompare 保持现有排序。 */
export function splitSkillCoreFiles(
  files: SkillFile[],
  isRoot: boolean
): { core: SkillFile[]; rest: SkillFile[] } {
  const isCore = (file: SkillFile) =>
    isRoot && skillCoreFileRank(file.path) < SKILL_CORE_FILE_ORDER.length
  const core = files
    .filter(isCore)
    .sort((a, b) => skillCoreFileRank(a.path) - skillCoreFileRank(b.path))
  const rest = files.filter((file) => !isCore(file))
  rest.sort((a, b) => a.path.localeCompare(b.path))
  return { core, rest }
}

export const skillFileName = (path: string) => path.split('/').pop() ?? path
