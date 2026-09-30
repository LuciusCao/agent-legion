/**
 * Studio 浮动功能岛（#799：去 AppBar 画布化——DAG 全屏为底板，原顶栏内容
 * 拆成两个半透明毛玻璃浮岛；#804 验收定案重组）：
 * - 左上「workflow 指挥中心」岛：返回、workspace 名（#804：去掉「/ 编辑
 *   工作流」modeText 与草稿基线文本）、版本选择器（紧跟标题）、状态 chip
 *   （CI 风格唯一状态表达：未发布变更 → 校验中… → ✓/✗，点击开校验报告
 *   抽屉；干净态不显示）、草稿保存瞬态文本（仅 保存中…/保存失败将重试/
 *   冲突警示；手动保存按钮退役，自动保存覆盖）+ 分隔线 + 生命周期动作
 *   （发布 contained 主按钮 + 仅 dirty 外露的 outlined 重置；校验按钮
 *   退役——保存成功后自动静默校验；只读态 返回/设为草稿）；
 * - 右上「纯图标」岛：Agent 面板开关 + 共享素材，视觉同质成组（用量入口
 *   已移除：实例级遥测与 workflow 编辑无语义关系，其余页面全局顶栏已有）。
 * z 900 与 Dock 同档——岛的关键操作不被 Dock 遮、Dock 盖 DAG 不受影响
 * （同 z 档按 DOM 序，Dock 的 Portal 更后）。
 */
import { IconButton, Tooltip } from '@mui/material'
import { useNavigate, useParams } from 'react-router-dom'
import { MaterialIcon } from '../../../components/MaterialIcon'
import { StudioAgentPanelToggle } from '../inspector/StudioAgentPanelToggle'
import { useStudioState, useStudioView } from './studioStateContext'
import { useWorkspaceDisplayName } from './useWorkspaceDisplayName'
import { WorkflowRevisionSelect } from './WorkflowRevisionSelect'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'
import { WorkflowStudioDraftSaveControlContainer } from './WorkflowStudioDraftSaveControl'
import { WorkflowStudioSharedMaterialsButton } from './WorkflowStudioSharedMaterialsDrawer'
import { WorkflowStudioStatusChip } from './WorkflowStudioStatusChip'
import { useIslandExclusiveWidth } from './useIslandExclusiveWidth'
import styles from './StudioCanvasIslands.module.css'

export function StudioCanvasIslands() {
  const studio = useStudioState()
  const view = useStudioView()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const title = useWorkspaceDisplayName(workspaceId)
  // P1 宽屏互斥（#804 codex 轮 2）：左岛 max-width = 画布列宽 - 右岛实测宽
  // - 间距，resize + ResizeObserver 驱动（见 useIslandExclusiveWidth）。
  const { identityRef, actionRef, identityMaxWidth } = useIslandExclusiveWidth()
  // 岛锚定在画布列内（#804 codex 轮 2 P2：原挂在整个分栏 scope 上会横跨
  // 详情列）：窄屏非画布页签的隐藏由画布列的响应式 CSS 承担（data-mobile-
  // panel display:none），顶边无需让位页签导航（画布本就在它下方）。
  const islandTop = 12
  const validationMessage = studio.validationMessage ?? ''

  return (
    <>
      {/* 左岛 = workflow 指挥中心（#804 定案排序）：返回 + workspace 名 +
          版本选择器 + 状态 chip + 保存瞬态文本 + 分隔线 + 动作组。窄屏
          降级：CSS 隐藏 secondary 件（标题/状态 chip/保存文本）与动作组
          的 outlined 次级按钮（重置），只留返回 + 版本选择器 + contained
          主按钮。 */}
      <div
        className={`${styles.island} ${styles.identity}`}
        ref={identityRef}
        style={{
          top: islandTop,
          maxWidth: identityMaxWidth ?? undefined,
        }}
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
        <span className={`${styles.title} ${styles.secondary}`} title={title}>
          {title}
        </span>
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
        <span className={styles.secondary}>
          <WorkflowStudioStatusChip
            readOnly={studio.readOnly}
            version={studio.revision?.version ?? null}
            dirty={studio.dirty}
            hasPreservedDraft={studio.hasPreservedDraft}
            summary={studio.compareSummary}
            compareState={studio.compareState}
            validating={studio.actionState === 'validating'}
            validationMessage={validationMessage}
            onShowChanges={() => view.setChangesPanelOpen(true)}
          />
        </span>
        <span className={styles.secondary}>
          <WorkflowStudioDraftSaveControlContainer />
        </span>
        <span className={styles.divider} aria-hidden="true" />
        <span className={styles.actionsGroup}>
          <WorkflowStudioCommandBarActions
            readOnly={studio.readOnly}
            dirty={studio.dirty}
            actionState={studio.actionState}
            canPublish={studio.canPublish}
            createsRevision={studio.compareSummary?.createsRevision}
            validationFailed={validationMessage.startsWith('校验失败')}
            onPublish={() => void studio.requestPublish()}
            onReset={studio.resetDefinition}
            backToDraft={studio.backToDraft}
            useViewedRevisionAsDraft={studio.useViewedRevisionAsDraft}
          />
        </span>
      </div>
      {/* 右岛 = 纯图标按钮组（Agent 面板开关 + 共享素材），视觉同质成组。
          用量入口移除（#799 重组：实例级遥测与 workflow 编辑无语义关系，
          其余页面全局顶栏已有）。 */}
      <div
        className={`${styles.island} ${styles.actions}`}
        ref={actionRef}
        style={{ top: islandTop }}
        data-testid="studio-action-island"
        aria-label="Workflow command bar"
      >
        <StudioAgentPanelToggle />
        <WorkflowStudioSharedMaterialsButton />
      </div>
    </>
  )
}
