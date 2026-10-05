/**
 * Studio 浮动功能岛（#799：去 AppBar 画布化——DAG 全屏为底板，原顶栏内容
 * 拆成两个半透明毛玻璃浮岛；#804 验收定案重组）：
 * - 左上「workflow 指挥中心」岛：返回、workspace 名（#804：去掉「/ 编辑
 *   工作流」modeText 与草稿基线文本）、版本选择器（紧跟标题；#770 只显示
 *   vN，hash 降级 tooltip，重置收进其菜单）、状态 chip
 *   （CI 风格唯一状态表达：未发布变更 → 校验中… → ✓/✗，点击开校验报告
 *   抽屉；干净态不显示）、草稿保存瞬态文本（仅 保存中…/保存失败将重试/
 *   冲突警示；手动保存按钮退役，自动保存覆盖）+ 分隔线 + 生命周期动作
 *   （发布 contained 主按钮；#770 起重置收进版本菜单不再外露；校验按钮
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
import { countNodeChanges } from '../canvas/workflowStudioDagChanges'
import { useStudioNarrowViewport } from './useStudioNarrowViewport'
import { useWorkspaceDisplayName } from './useWorkspaceDisplayName'
import { WorkflowRevisionSelect } from './WorkflowRevisionSelect'
import { WorkflowStudioCommandBarActions } from './WorkflowStudioCommandBarActions'
import { WorkflowStudioDraftSaveControlContainer } from './WorkflowStudioDraftSaveControl'
import { WorkflowStudioSharedMaterialsButton } from './WorkflowStudioSharedMaterialsButton'
import { studioPublishTooltip } from './studioIslandPublishTooltip'
import { WorkflowStudioStatusChip } from './WorkflowStudioStatusChip'
import { useIslandExclusiveWidth } from './useIslandExclusiveWidth'
import styles from './StudioCanvasIslands.module.css'

// inert 透传对象（React 18 类型不识 inert，按未知属性透传；语义见组件内
// D4/P2-1 注释）。
const INERT = { inert: '' } as { inert?: string }

export function StudioCanvasIslands() {
  const studio = useStudioState()
  const view = useStudioView()
  const { workspaceId } = useParams<{ workspaceId: string }>()
  const navigate = useNavigate()
  const title = useWorkspaceDisplayName(workspaceId)
  // 重置出口（轮 4 P2-D 窄屏起步，#770 顶栏减法推广到全宽度）：低频破坏性
  // 动作不再外露按钮，统一收进版本选择器菜单（带确认）。narrow 仅剩
  // inert 分级用途。
  const narrow = useStudioNarrowViewport()
  // P1 宽屏互斥（#804 codex 轮 2）：左岛 max-width = 画布列宽 - 右岛实测宽
  // - 间距，resize + ResizeObserver 驱动（见 useIslandExclusiveWidth）。
  const { identityRef, actionRef, identityMaxWidth } = useIslandExclusiveWidth()
  // 岛锚定在画布列内（#804 codex 轮 2 P2：原挂在整个分栏 scope 上会横跨
  // 详情列）：窄屏非画布页签的隐藏由画布列的响应式 CSS 承担（data-mobile-
  // panel display:none），顶边无需让位页签导航（画布本就在它下方）。
  const islandTop = 12
  const validationMessage = studio.validationMessage ?? ''
  // 发布禁用原因文案的规则与优先级在 studioIslandPublishTooltip.ts（轮 3
  // P2 校验绑定 / 轮 4 P1-1 结构 vs 传输 / 轮 6 H2 冲突 / H4 compare 失败）。
  const publishTooltip = studioPublishTooltip({
    dirty: studio.dirty,
    inConflict: studio.draftSave?.conflict === true,
    compareError: studio.compareState === 'error',
    validationMessage,
  })
  // #812 对抗轮 D4 + P2-1：抽屉打开期间被 paper 物理遮住的岛触发器加
  // inert，堵住「Tab 聚焦被遮按钮并激活」的路径（inert 一次阻断指针/键盘/
  // 读屏；React 18 类型与运行时都不识 inert，按未知属性透传空字符串，同
  // WorkflowNodeAgentGate 的既有写法）。分级：宽屏 720px 抽屉只遮右岛，
  // 可见的左岛（返回/版本/发布）保持可交互——persistent 抽屉是非模态的；
  // 窄屏抽屉全宽覆盖，双岛一起 inert。
  const drawerOpen = Boolean(studio.selectedNodeKey) || view.materialsOpen

  return (
    <>
      {/* 左岛 = workflow 指挥中心（#804 定案排序）：返回 + workspace 名 +
          版本选择器 + 状态 chip + 保存瞬态文本 + 分隔线 + 动作组。窄屏
          降级：CSS 隐藏 secondary（标题，以及保存控件内部的瞬态文本）与
          conditional（chip）及动作组的 outlined 次级按钮，只留
          返回 + 版本选择器 + contained 主按钮；冲突警示/冲突操作出口
          窄屏保留（codex 轮 3 P1，DraftSaveControl 自行分流）。间距纪律：
          岛级 flex gap 一套机制，conditional/passthrough 用
          display:contents——条件元素缺席时不留幻影 gap。 */}
      <div
        className={`${styles.island} ${styles.identity}`}
        ref={identityRef}
        style={{
          top: islandTop,
          maxWidth: identityMaxWidth ?? undefined,
        }}
        data-testid="studio-identity-island"
        aria-label="工作流身份与导航"
        {...(drawerOpen && narrow ? INERT : {})}
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
          onResetDraft={
            studio.dirty && !studio.readOnly
              ? studio.resetDefinition
              : undefined
          }
        />
        <span className={styles.passthrough}>
          <WorkflowStudioStatusChip
            readOnly={studio.readOnly}
            version={studio.revision?.version ?? null}
            dirty={studio.dirty}
            hasPreservedDraft={studio.hasPreservedDraft}
            summary={studio.compareSummary}
            compareState={studio.compareState}
            validating={studio.validating}
            validationMessage={validationMessage}
            onShowChanges={() => view.setChangesPanelOpen(true)}
          />
        </span>
        <span className={styles.passthrough}>
          <WorkflowStudioDraftSaveControlContainer />
        </span>
        <span className={styles.divider} aria-hidden="true" />
        <span className={styles.actionsGroup}>
          <WorkflowStudioCommandBarActions
            readOnly={studio.readOnly}
            publishing={studio.publishing}
            validating={studio.validating}
            canPublish={studio.canPublish}
            createsRevision={studio.compareSummary?.createsRevision}
            publishTooltip={publishTooltip}
            onPublish={() => void studio.requestPublish()}
            backToDraft={studio.backToDraft}
            confirmAdoptDraft={
              studio.dirty || Boolean(countNodeChanges(studio.compareSummary))
            }
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
        {...(drawerOpen ? INERT : {})}
      >
        <StudioAgentPanelToggle />
        <WorkflowStudioSharedMaterialsButton />
      </div>
    </>
  )
}
