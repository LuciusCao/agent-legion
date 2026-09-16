import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import FolderOutlinedIcon from '@mui/icons-material/FolderOutlined'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import { Button } from '@mui/material'
import type { SkillFile } from '../../../types/agentCatalogTypes'
import type { SkillDirNode } from './skillFileTree'
import { skillFileName, splitSkillCoreFiles } from './skillFileTree'
import styles from './WorkflowSkillFileList.module.css'

/** 目录树的一个节点：目录行（可折叠）+ 子目录/文件行（递归，按名称排序）。
 * depth 是本行缩进层级；根节点无行，其子节点与根同级（不额外缩进）。
 * 根目录的核心文件（SKILL.md / contract.yaml，置顶优先级表见
 * skillFileTree.ts，#676）排在子目录之前；目录与其余文件的排序不变。 */
export function WorkflowSkillFileTreeNode(props: {
  dir: SkillDirNode
  depth: number
  collapsed: ReadonlySet<string>
  selected: SkillFile | undefined
  onToggleDir: (path: string) => void
  onSelect: (path: string) => void
}) {
  const { dir } = props
  const isCollapsed = props.collapsed.has(dir.path)
  const childDepth = dir.path ? props.depth + 1 : props.depth
  const { core, rest } = splitSkillCoreFiles(dir.files, dir.path === '')
  const renderFile = (file: SkillFile) => (
    <Button
      className={styles.fileButton}
      color="inherit"
      key={file.path}
      style={{ paddingLeft: childDepth * 14 }}
      startIcon={<DescriptionOutlinedIcon />}
      variant={props.selected?.path === file.path ? 'outlined' : 'text'}
      onClick={() => props.onSelect(file.path)}
    >
      <span>{skillFileName(file.path)}</span>
    </Button>
  )
  return (
    <div>
      {dir.path && (
        <Button
          className={styles.dirButton}
          color="inherit"
          style={{ paddingLeft: props.depth * 14 }}
          startIcon={
            <>
              {isCollapsed ? (
                <KeyboardArrowRightIcon fontSize="small" />
              ) : (
                <KeyboardArrowDownIcon fontSize="small" />
              )}
              <FolderOutlinedIcon fontSize="small" />
            </>
          }
          aria-expanded={!isCollapsed}
          onClick={() => props.onToggleDir(dir.path)}
        >
          <span>{dir.name}</span>
        </Button>
      )}
      {!isCollapsed && (
        <>
          {core.map(renderFile)}
          {[...dir.dirs]
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((child) => (
              <WorkflowSkillFileTreeNode
                key={child.path}
                dir={child}
                depth={childDepth}
                collapsed={props.collapsed}
                selected={props.selected}
                onToggleDir={props.onToggleDir}
                onSelect={props.onSelect}
              />
            ))}
          {rest.map(renderFile)}
        </>
      )}
    </div>
  )
}
