/** 节点详情抽屉（#804 定案抽屉化：右侧分栏退役，画布永远全宽；节点编辑
 * 与共享素材统一为右侧 Drawer 浮层）：
 * - 宽屏 720px 浮层（圆角/毛玻璃/阴影，与岛/Dock 同一浮层语言），窄屏
 *   全宽覆盖；
 * - 头部 = 节点名 + 类型选择器/徽标 + ✕ 关闭（inspector 自带头栏承接，
 *   无「← 返回 + 面包屑」——返回语义即关抽屉，workflow 身份在左岛）；
 * - 预览子态（运行 Prompt / 技能文件）无面包屑，给一条精简返回条
 *   「← 节点详情」回 inspector，✕ 始终关抽屉；
 * - ✕ / Esc / 点遮罩都是关闭（清空选中节点）。
 */
import { Close } from '@mui/icons-material'
import { Drawer, IconButton, Tooltip } from '@mui/material'
import { WorkflowStudioSaveWarningBanner } from '../shared/WorkflowStudioSaveWarningBanner'
import { useDrawerEscape } from '../shared/useDrawerEscape'
import { useStudioState, useStudioView } from '../shared/studioStateContext'
import { selectedNodeDetails } from '../shared/workflowStudioModel'
import { useNodeDetailPreview } from './useNodeDetailPreview'
import { WorkflowNodeDetailBody } from './WorkflowNodeDetailBody'
import styles from './WorkflowNodeDetailDrawer.module.css'

export function WorkflowNodeDetailDrawer() {
  const studio = useStudioState()
  const view = useStudioView()
  const nodeKey = studio.selectedNodeKey
  const close = () => studio.setSelectedNodeKey(null)
  // nodeKey 为 null 时 preview hook 也需要稳定调用（hooks 纪律）；nodeKey
  // 变化时 hook 内部自行重置预览态。
  const preview = useNodeDetailPreview(nodeKey ?? '')
  const node = nodeKey
    ? selectedNodeDetails(studio.workflow, nodeKey)?.node
    : undefined
  // persistent 不走 Modal——Esc 关闭自行承接（capture + preventDefault，
  // Dock 的 Esc 处理器见 defaultPrevented 跳过）；与共享素材抽屉共存时由
  // 抽屉栈仲裁，只关栈顶（useDrawerEscape/drawerStack）。suppressed
  // （#812 D3：窄屏非画布页签，所在面板 display:none）时隐藏抽屉不占
  // 栈位。返回的 zIndex 挂到 paper：栈位映射视觉层级，Esc 栈序 == 视觉序。
  const escapeSuppressed = view.narrow && view.mobilePanel !== 'graph'
  const paperZIndex = useDrawerEscape(nodeKey !== null, close, escapeSuppressed)

  return (
    <Drawer
      anchor="right"
      open={nodeKey !== null}
      onClose={close}
      slotProps={{
        paper: { className: styles.paper, style: { zIndex: paperZIndex } },
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
      {nodeKey ? (
        <div className={styles.body} aria-label="节点详情">
          {/* 轮 4 P2-F：抽屉盖住左岛期间，保存失败/冲突警示在抽屉内嵌横幅
              保持可见（同源状态）。 */}
          <WorkflowStudioSaveWarningBanner />
          {/* 预览子态的精简返回条：inspector 头栏在预览时被预览面板替换，
              预览的退出入口由这里承接（✕ 始终关抽屉）。 */}
          {preview.activeKind ? (
            <div className={styles.previewBar}>
              <button
                type="button"
                className={styles.previewBack}
                aria-label="返回节点详情"
                onClick={preview.closePreview}
              >
                ← 节点详情
              </button>
              <span className={styles.previewTitle}>
                {node?.label ?? nodeKey}
                {preview.crumbs}
              </span>
              <Tooltip title="关闭">
                <IconButton size="small" aria-label="关闭" onClick={close}>
                  <Close fontSize="small" />
                </IconButton>
              </Tooltip>
            </div>
          ) : null}
          <WorkflowNodeDetailBody
            workflow={studio.workflow}
            nodeKey={nodeKey}
            agentCatalog={studio.agentCatalog}
            agentCatalogSettle={studio.agentCatalogSettle}
            definitionYaml={studio.definitionYaml}
            setDefinitionYaml={studio.setDefinitionYaml}
            compareSummary={studio.compareSummary}
            readOnly={studio.readOnly}
            activeKind={preview.activeKind}
            onShowPreview={preview.showPreview}
            onClose={close}
          />
        </div>
      ) : null}
    </Drawer>
  )
}
