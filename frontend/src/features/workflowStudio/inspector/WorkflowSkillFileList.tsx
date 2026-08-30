import { useState } from 'react'
import type { SkillFile } from '../../../types/agentCatalogTypes'
import { buildSkillFileTree } from './skillFileTree'
import { WorkflowSkillFileTreeNode } from './WorkflowSkillFileTreeNode'
import panelStyles from './WorkflowSkillPreviewPanel.module.css'

/** 技能预览左侧的文件目录树：目录可折叠（默认全部展开），文件按目录层级
 * 缩进，选中态保持 outlined 样式。nav 容器沿用 panel 的 fileList 样式。 */
export function WorkflowSkillFileList(props: {
  files: SkillFile[]
  selected: SkillFile | undefined
  onSelect: (path: string) => void
}) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set())
  const toggleDir = (path: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  return (
    <nav className={panelStyles.fileList} aria-label="技能文件">
      <WorkflowSkillFileTreeNode
        dir={buildSkillFileTree(props.files)}
        depth={0}
        collapsed={collapsed}
        selected={props.selected}
        onToggleDir={toggleDir}
        onSelect={props.onSelect}
      />
    </nav>
  )
}
