import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import FolderOutlinedIcon from '@mui/icons-material/FolderOutlined'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import { Button } from '@mui/material'
import type { SkillFile } from '../../../types/agentCatalogTypes'
import type { SkillDirNode } from './skillFileTree'
import { skillFileName } from './skillFileTree'
import styles from './WorkflowSkillFileList.module.css'

/** 目录树的一个节点：目录行（可折叠）+ 子目录/文件行（递归，按名称排序）。
 * depth 是本行缩进层级；根节点无行，其子节点与根同级（不额外缩进）。 */
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
          {[...dir.files]
            .sort((a, b) => a.path.localeCompare(b.path))
            .map((file) => (
              <Button
                className={styles.fileButton}
                color="inherit"
                key={file.path}
                style={{ paddingLeft: childDepth * 14 }}
                startIcon={<DescriptionOutlinedIcon />}
                variant={
                  props.selected?.path === file.path ? 'outlined' : 'text'
                }
                onClick={() => props.onSelect(file.path)}
              >
                <span>{skillFileName(file.path)}</span>
              </Button>
            ))}
        </>
      )}
    </div>
  )
}
