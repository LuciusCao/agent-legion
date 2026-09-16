import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Close, FolderSharedOutlined } from '@mui/icons-material'
import { Drawer, IconButton, Tooltip, Typography } from '@mui/material'
import {
  getWorkspaceSharedMaterials,
  propagateWorkspaceSharedMaterials,
} from '../../../api'
import type { SharedMaterialPropagateSkillResult } from '../../../api'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import {
  buildSharedMaterialFileRows,
  type SharedMaterialFileRow,
} from './sharedMaterialsRows'
import {
  groupSharedMaterialFileRows,
  rowDisplayPath,
} from './sharedMaterialsGroups'
import { SharedMaterialFileContentDialog } from './WorkflowStudioSharedMaterialsFileDialog'
import { SharedMaterialFileRowView } from './WorkflowStudioSharedMaterialsFileRow'
import { SharedMaterialsPropagateConfirmDialog } from './WorkflowStudioSharedMaterialsPropagateDialog'
import styles from './WorkflowStudioSharedMaterialsDrawer.module.css'

const PROPAGATE_STATUS_LABELS: Record<
  SharedMaterialPropagateSkillResult['status'],
  string
> = {
  synced: '已同步',
  skipped: '已跳过',
  failed: '失败',
}

function SharedMaterialsDrawer({
  workspaceId,
  onClose,
}: {
  workspaceId: string
  onClose: () => void
}) {
  const [openPath, setOpenPath] = useState<string | null>(null)
  const [confirmRow, setConfirmRow] = useState<SharedMaterialFileRow | null>(
    null
  )
  const [results, setResults] = useState<
    SharedMaterialPropagateSkillResult[] | null
  >(null)
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: extraQueryKeys.workspaceSharedMaterials(workspaceId),
    queryFn: () => getWorkspaceSharedMaterials(workspaceId),
  })
  const mutation = useMutation({
    mutationFn: (sources: string[]) =>
      propagateWorkspaceSharedMaterials(workspaceId, sources),
    onSuccess: (response) => {
      setConfirmRow(null)
      setResults(response.results ?? [])
      void queryClient.invalidateQueries({
        queryKey: extraQueryKeys.workspaceSharedMaterials(workspaceId),
      })
    },
  })

  const rows = data ? buildSharedMaterialFileRows(data) : []
  const groups = groupSharedMaterialFileRows(rows)

  return (
    <Drawer anchor="right" open onClose={onClose}>
      <div className={styles.panel}>
        <div className={styles.header}>
          <Typography variant="h6" component="div" className={styles.title}>
            Skill 共享材料
          </Typography>
          <Tooltip title="关闭">
            <IconButton
              edge="end"
              onClick={onClose}
              aria-label="close shared materials panel"
            >
              <Close />
            </IconButton>
          </Tooltip>
        </div>
        <p className={styles.hint}>
          本 workspace 各 skill 共享的参考材料（<code>_shared</code>
          目录）。修改共享源后副本不会自动传播：点行内「同步并打
          tag」把最新副本写进映射 skill 的仓库（逐个 commit + 新
          tag），或逐个重存 skill。
        </p>
        {error && (
          <p className={styles.error} role="alert">
            {(error as Error).message}
          </p>
        )}
        {mutation.error && (
          <p className={styles.error} role="alert">
            {(mutation.error as Error).message}
          </p>
        )}
        {results && (
          <div className={styles.results} role="status">
            <div className={styles.resultsHeader}>
              <span>同步结果</span>
              <IconButton
                size="small"
                aria-label="清除同步结果"
                onClick={() => setResults(null)}
              >
                <Close fontSize="small" />
              </IconButton>
            </div>
            <ul className={styles.resultsList}>
              {results.map((entry) => (
                <li key={entry.skill}>
                  {entry.skill}：{PROPAGATE_STATUS_LABELS[entry.status]}
                  {entry.tag ? ` → ${entry.tag}` : ''}
                  {entry.detail ? `（${entry.detail}）` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
        {isLoading ? (
          <p className={styles.empty}>加载中…</p>
        ) : data?.map == null ? (
          <p className={styles.empty}>
            本 workspace 尚未启用共享材料。由 Studio Agent 经 skills-shared
            工具写入 <code>_shared/map.json</code> 与 references/scripts
            文件后，此处会展示文件清单与同步状态。
          </p>
        ) : (
          <>
            {/* map.json 置顶行：样式同普通文件行，行下用途说明；无徽标、
                无传播动作、不参与分组，点开看原文。 */}
            <ul className={styles.list}>
              <li
                className={styles.listItem}
                data-testid="shared-material-map.json"
              >
                <div className={styles.rowTop}>
                  <button
                    type="button"
                    className={styles.fileButton}
                    onClick={() => setOpenPath('map.json')}
                  >
                    <code className={styles.itemLabel}>map.json</code>
                  </button>
                </div>
                <p className={styles.mapDesc}>
                  声明材料 → skills 的映射关系；保存 skill
                  版本或手动传播时按此同步共享副本。
                </p>
              </li>
            </ul>
            {rows.length === 0 ? (
              <p className={styles.empty}>
                _shared 下暂无可读文件，map.json 也未声明任何映射。
              </p>
            ) : (
              groups.map((group) => (
                <section key={group.key} className={styles.group}>
                  <div className={styles.groupHeader}>
                    <h3 className={styles.groupTitle}>{group.label}</h3>
                    <span className={styles.groupDesc}>
                      {group.description}
                    </span>
                  </div>
                  <ul className={styles.list}>
                    {group.rows.map((row) => (
                      <SharedMaterialFileRowView
                        key={row.path}
                        row={row}
                        displayPath={rowDisplayPath(group.key, row)}
                        propagating={mutation.isPending}
                        onOpenFile={setOpenPath}
                        onPropagate={setConfirmRow}
                      />
                    ))}
                  </ul>
                </section>
              ))
            )}
          </>
        )}
        {confirmRow && (
          <SharedMaterialsPropagateConfirmDialog
            row={confirmRow}
            pending={mutation.isPending}
            onCancel={() => setConfirmRow(null)}
            onConfirm={() => mutation.mutate([confirmRow.path])}
          />
        )}
        {openPath && (
          <SharedMaterialFileContentDialog
            workspaceId={workspaceId}
            path={openPath}
            onClose={() => setOpenPath(null)}
          />
        )}
      </div>
    </Drawer>
  )
}

/**
 * Studio 顶栏「共享材料」入口（issue #643）：图标按钮 + 右侧 Drawer
 * 展示本 workspace 的 _shared 文件清单（行内 drift 徽标），行内提供
 * 「同步并打 tag」传播动作（#673）。workspaceId 取路由参数；无 Router
 * 上下文（测试直渲染 CommandBar）时退化为 {}，抽屉不打开即不触发任何查询。
 */
export function WorkflowStudioSharedMaterialsButton() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const [open, setOpen] = useState(false)
  return (
    <>
      <Tooltip title="Skill 共享材料">
        <IconButton
          size="small"
          aria-label="Skill 共享材料"
          onClick={() => setOpen(true)}
        >
          <FolderSharedOutlined fontSize="small" />
        </IconButton>
      </Tooltip>
      {open && workspaceId && (
        <SharedMaterialsDrawer
          workspaceId={workspaceId}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}
