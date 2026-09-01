/**
 * #333 全局 onboarding 的一次性标记：admin bootstrap 后、进入产品前展示
 * 全局清单；页面上任何离开动作（进入产品 / 跳过 / 去全局设置）都写入
 * dismissed，之后可从全局设置侧栏的「全局初始化清单」入口回补。
 * localStorage 不可用（隐私模式等）时读侧按已 dismiss 放行、写侧静默，
 * 不把用户拦在清单页出不去。
 */
const DISMISS_KEY = 'agent-legion:global-onboarding-dismissed'

export const GLOBAL_ONBOARDING_PATH = '/admin/onboarding'

export function isGlobalOnboardingDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISS_KEY) === '1'
  } catch {
    return true
  }
}

export function dismissGlobalOnboarding(): void {
  try {
    window.localStorage.setItem(DISMISS_KEY, '1')
  } catch {
    // 忽略：写不进去时放行，下次访问仍会看到清单（可再跳过）。
  }
}
