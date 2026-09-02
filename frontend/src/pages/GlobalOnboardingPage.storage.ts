/**
 * #333 全局 onboarding 的一次性标记：admin bootstrap 后、进入产品前展示
 * 欢迎页；页面上任何离开动作（进入产品 / 手动添加 agent）都写入
 * dismissed。回补入口已随全局设置侧栏退役（bootstrap 跳转是唯一正常
 * 入口），读侧 API 一并移除。localStorage 不可用（隐私模式等）时写侧
 * 静默，不把用户拦在欢迎页出不去。
 */
const DISMISS_KEY = 'agent-legion:global-onboarding-dismissed'

export const GLOBAL_ONBOARDING_PATH = '/admin/onboarding'

export function dismissGlobalOnboarding(): void {
  try {
    window.localStorage.setItem(DISMISS_KEY, '1')
  } catch {
    // 忽略：写不进去时放行，下次访问仍会看到欢迎页（可再进入产品）。
  }
}
