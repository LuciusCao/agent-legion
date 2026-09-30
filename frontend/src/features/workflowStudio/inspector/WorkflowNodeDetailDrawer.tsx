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
import { useStudioState } from '../shared/studioStateContext'
import { selectedNodeDetails } from '../shared/workflowStudioModel'
import { useNodeDetailPreview } from './useNodeDetailPreview'
import { WorkflowNodeDetailBody } from './WorkflowNodeDetailBody'
import styles from './WorkflowNodeDetailDrawer.module.css'

export function WorkflowNodeDetailDrawer() {
  const studio = useStudioState()
  const nodeKey = studio.selectedNodeKey
  const close = () => studio.setSelectedNodeKey(null)
  // nodeKey 为 null 时 preview hook 也需要稳定调用（hooks 纪律）；nodeKey
  // 变化时 hook 内部自行重置预览态。
  const preview = useNodeDetailPreview(nodeKey ?? '')
  const node = nodeKey
    ? selectedNodeDetails(studio.workflow, nodeKey)?.node
    : undefined

  return (
    <Drawer
      anchor="right"
      open={nodeKey !== null}
      onClose={close}
      slotProps={{ paper: { className: styles.paper } }}
    >
      {nodeKey ? (
        <div className={styles.body} aria-label="节点详情">
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
