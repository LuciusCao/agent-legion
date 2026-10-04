/** Studio 右侧抽屉（节点详情/共享素材）的占用宽度：720 + 8 margin
 * （studioDrawerFloat.module.css 的 .drawerPaper 同值——改一边必须同步
 * 另一边），Dock 避让（offsetForRightInset 的 rightInset）读它。 */
export const STUDIO_DRAWER_RIGHT_INSET = 728

/** 窄屏抽屉顶边让位的 CSS 变量（#817 方向 a）：值是页签行实测底边
 * （useStudioDrawerPaperStyle 写到 paper 内联样式），
 * studioDrawerFloat.module.css 只在 ≤900px 断点里消费它——宽屏不读，
 * 宽屏几何不变。 */
export const STUDIO_DRAWER_TOP_INSET_VAR = '--studio-drawer-top-inset'
