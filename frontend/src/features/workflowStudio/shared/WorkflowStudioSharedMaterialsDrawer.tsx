import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useStudioView } from './studioStateContext'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Close } from '@mui/icons-material'
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
import { WorkflowStudioSaveWarningBanner } from './WorkflowStudioSaveWarningBanner'
import { useStudioDrawerPaperStyle } from './useStudioDrawerPaperStyle'
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
  hidden,
}: {
  workspaceId: string
  onClose: () => void
  hidden: boolean
}) {
  const [openPath, setOpenPath] = useState<string | null>(null)
  const [confirmRow, setConfirmRow] = useState<SharedMaterialFileRow | null>(
    null
  )
  const [results, setResults] = useState<
    SharedMaterialPropagateSkillResult[] | null
  >(null)
  // persistent 不走 Modal——Esc 关闭自行承接（capture + preventDefault）；
  // 与节点详情抽屉共存时由抽屉栈仲裁，只关栈顶（useDrawerEscape/drawerStack）。
  // hidden（#812 P2-2：窄屏非画布页签——抽屉挂在 SplitLayout 层，不随画布列
  // display:none，隐藏要自带）：paper display:none 不卸载（打开状态/内部
  // 草稿保留），同时出 Esc 栈不占栈位。paper 样式里的 zIndex 是栈位映射的
  // 视觉层级，Esc 栈序 == 视觉序（#812 对抗轮 D2）。
  // #817：paper 样式同时带窄屏顶边让位（页签行之下，Agent 页签可点）。
  const paperStyle = useStudioDrawerPaperStyle(true, onClose, hidden)
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
    <Drawer
      anchor="right"
      open
      onClose={onClose}
      slotProps={{
        paper: {
          className: styles.paper,
          // hidden：display:none 而非卸载——打开状态与抽屉内草稿保留。
          style: paperStyle,
        },
      }}
      /* 轮 8 P2：非模态——persistent variant 不走 Modal（无遮罩/不圈禁
         焦点/不锁滚动/不 aria-hidden 兄弟），Dock 与画布保持可交互；
         ✕/Esc 关闭，浮层定位由 paper CSS 承担。 */
      variant="persistent"
      // hotfix：persistent 的 docked 根节点常驻 DOM 且参与 SplitLayout 的
      // grid——其 Slide 包装在流内有高度，grid 行被均分（画布只剩半屏）。
      // paper 是 position:fixed 自定位，根节点零价值：display:contents
      // 退出布局流（抽屉开关/过渡/Esc 语义不变）。
      sx={{ display: 'contents' }}
    >
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
        {/* 轮 4 P2-F：抽屉盖住左岛期间的保存/冲突警示内嵌横幅。 */}
        <WorkflowStudioSaveWarningBanner />
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
            rows={rows}
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
 * 共享素材抽屉本体（issue #643：展示本 workspace 的 _shared 文件清单——行内
 * drift 徽标 + 「同步并打 tag」传播动作 #673）。
 * 挂载点纪律（#812 对抗轮 D1）：必须挂在 SplitLayout 层（与节点详情抽屉
 * 平级），不能挂在岛内——岛的 backdrop-filter 会让祖先成为 fixed paper 的
 * 包含块，挂在岛内会把抽屉渲染成钉在岛角落的碎片。触发按钮留在岛内
 * （WorkflowStudioSharedMaterialsButton.tsx），开合状态经
 * StudioViewContext 共享；workspaceId 取路由参数，无匹配参数时抽屉不打开。
 */
export function WorkflowStudioSharedMaterialsDrawer() {
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const view = useStudioView()
  const { materialsOpen: open, setMaterialsOpen: setOpen } = view
  // D3/P2-2：窄屏非画布页签时抽屉隐藏（paper display:none、不占 Esc
  // 栈位）——抽屉挂在 SplitLayout 层，不随画布列 display:none。
  const hidden = view.narrow && view.mobilePanel !== 'graph'
  if (!open || !workspaceId) return null
  return (
    <SharedMaterialsDrawer
      workspaceId={workspaceId}
      onClose={() => setOpen(false)}
      hidden={hidden}
    />
  )
}
