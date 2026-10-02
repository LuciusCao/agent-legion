/**
 * 全局浮层 z-index 刻度（#818）：仓库自绘浮层的层级单一声明点，各浮层从
 * 这里取值，不再各写魔数。自下而上：
 *
 *   页面内容（JobFilterBar 10）< AppBar 100
 *   < Studio 浮动岛 / Agent Dock 900–999（栈位映射，dockStack.ts）
 *   < DagFullscreenDialog 1000 < TokenUsageDialog 1190/1200
 *   < Studio 右侧抽屉 1200–1289（栈位映射，drawerStack.ts）
 *   < Toast 1290
 *   < MUI Modal 1300（Dialog/Menu/Popover）< MUI Tooltip 1500
 *
 * Toast 是瞬时反馈（pointer-events:none，不拦交互），必须压过 persistent
 * 抽屉（#818：此前 1000 < 抽屉 1200，被右侧抽屉横向裁半），但仍低于一切
 * MUI 模态——模态打开时它是用户的焦点层，不该被底部通知盖住。抽屉栈的
 * 上限因此从 1299 收到 1289，给 Toast 留位（实际同时打开的抽屉最多两个）。
 * MUI 自身的层级来自 theme.zIndex（modal 1300），刻度与它的相对关系由
 * zLayers.test.ts 钉住。
 */
export const Z_LAYERS = {
  /** Agent Dock 栈位映射基值（栈底）。 */
  dockBase: 900,
  /** Agent Dock 栈位映射上限（低于 DagFullscreenDialog / Toast）。 */
  dockMax: 999,
  /** Studio 右侧抽屉（节点详情/共享素材）栈位映射基值。 */
  studioDrawerBase: 1200,
  /** Studio 右侧抽屉栈位映射上限（低于 Toast）。 */
  studioDrawerMax: 1289,
  /** 全局 Toast（压过抽屉，低于 MUI Modal）。 */
  toast: 1290,
} as const
