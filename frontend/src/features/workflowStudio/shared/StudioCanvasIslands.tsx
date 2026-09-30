/**
 * Studio 浮动功能岛（#799：去 AppBar 画布化——DAG 全屏为底板，原顶栏内容
 * 拆成两个半透明毛玻璃浮岛）：
 * - 左上「身份/导航」岛：返回、workflow 名 + 版本状态 modeText、状态 chip
 *   （变更摘要入口）、版本选择器、草稿保存控件；
 * - 右上「操作」岛：Agent 面板开关、用量入口、校验/发布新版本（contained
 *   主按钮外露）/重置、共享材料。
 * 窄屏（≤900px）保留页签导航为顶部 chrome，岛顶边让开其实测底边
 * （useStudioMobileNavHeight，与 Dock topInsetExtra 同源）并收成紧凑形态
 * （省略标题/modeText 文本，图标与控件保留）。z 900 与 Dock 同档——岛的
 * 关键操作不被 Dock 遮、Dock 盖 DAG 不受影响（同 z 档按 DOM 序，Dock 的
 * Portal 更后）。岛的拼装全部复用原 CommandBar 的组件与 props 映射
 * （WorkflowStudioCommandBarContainer 同款数据源），语义零迁移成本。
 */
import { IconButton, Tooltip } from '@mui/material'
import { useNavigate, useParams } from 'react-router-dom'
import { MaterialIcon } from '../../../components/MaterialIcon'
import { StudioAgentPanelToggle } from '../inspector/StudioAgentPanelToggle'
import { useStudioState, useStudioView } from './studioStateContext'
import { useStudioMobileNavHeight } from './useStudioMobileNavHeight'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'
import { useWorkflowStudioAppTitle } from './useWorkflowStudioAppTitle'
import { WorkflowRevisionSelect } from './WorkflowRevisionSelect'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'
import { WorkflowStudioDraftSaveControlContainer } from './WorkflowStudioDraftSaveControl'
import { WorkflowStudioSharedMaterialsButton } from './WorkflowStudioSharedMaterialsDrawer'
import { WorkflowStudioStatusChip } from './WorkflowStudioStatusChip'
import styles from './StudioCanvasIslands.module.css'

export function StudioCanvasIslands() {
  const studio = useStudioState()
  const view = useStudioView()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const title = useWorkflowStudioAppTitle(workspaceId)
  const narrow = useStudioNarrowViewport()
  const mobileNavHeight = useStudioMobileNavHeight()
  // 窄屏：页签导航是顶部 chrome，岛顶边让开其实测底边（宽屏实测 0）。
  const islandTop = (narrow ? mobileNavHeight : 0) + 12

  // 窄屏的编辑器/Agent 页签是全屏面板切换——岛只在画布页签浮出，不盖
  // 编辑器/详情。
  if (narrow && view.mobilePanel !== 'graph') return null

  const hash = studio.revision?.definition_hash?.slice(0, 8) ?? '--------'
  const modeText =
    studio.viewMode === 'revision'
      ? `查看 v${studio.revision?.version ?? '-'} · ${hash} · 只读`
      : `基于 v${studio.activeRevision?.version ?? '-'} 的草稿`

  return (
    <>
      <div
        className={`${styles.island} ${styles.identity}`}
        style={{ top: islandTop }}
        data-testid="studio-identity-island"
        aria-label="工作流身份与导航"
      >
        <Tooltip title="返回">
          <IconButton
            size="small"
            aria-label="返回"
            data-testid="app-bar-back"
            onClick={() =>
              navigate(workspaceId ? `/workspaces/${workspaceId}` : '/')
            }
          >
            <MaterialIcon name="arrow_back" />
          </IconButton>
        </Tooltip>
        <span className={styles.title} title={title}>
          {title}
        </span>
        <span className={styles.modeText}>{modeText}</span>
        <WorkflowStudioStatusChip
          readOnly={studio.readOnly}
          version={studio.revision?.version ?? null}
          dirty={studio.dirty}
          hasPreservedDraft={studio.hasPreservedDraft}
          summary={studio.compareSummary}
          compareState={studio.compareState}
          onShowChanges={() => view.setChangesPanelOpen(true)}
        />
        <WorkflowRevisionSelect
          revisions={studio.revisions}
          activeRevisionId={studio.activeRevision?.id}
          selectedRevisionId={studio.selectedRevisionId}
          currentVersion={
            studio.revision?.version ?? studio.activeRevision?.version
          }
          currentHash={
            studio.revision?.definition_hash ??
            studio.activeRevision?.definition_hash ??
            null
          }
          disabled={studio.isLoadingRevision}
          error={studio.revisionLoadError}
          onSelectRevision={studio.selectRevision}
        />
        <WorkflowStudioDraftSaveControlContainer />
      </div>
      <div
        className={`${styles.island} ${styles.actions}`}
        style={{ top: islandTop }}
        data-testid="studio-action-island"
        aria-label="Workflow command bar"
      >
        <StudioAgentPanelToggle />
        <Tooltip title="Token 使用分析">
          <IconButton
            size="small"
            aria-label="Token 使用分析"
            onClick={() =>
              workspaceId && navigate(`/workspaces/${workspaceId}/token-usage`)
            }
          >
            <MaterialIcon name="analytics" />
          </IconButton>
        </Tooltip>
        <WorkflowStudioCommandBarActions
          readOnly={studio.readOnly}
          dirty={studio.dirty}
          actionState={studio.actionState}
          canSubmit={studio.canSubmit}
          canPublish={studio.canPublish}
          createsRevision={studio.compareSummary?.createsRevision}
          onValidate={() => void view.validateAndShowResult()}
          onPublish={() => void studio.requestPublish()}
          onReset={studio.resetDefinition}
          backToDraft={studio.backToDraft}
          useViewedRevisionAsDraft={studio.useViewedRevisionAsDraft}
        />
        <WorkflowStudioSharedMaterialsButton />
      </div>
    </>
  )
}
