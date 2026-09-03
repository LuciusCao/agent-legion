import { vi } from 'vitest'

/** #416：agent 发布请求字段的默认形状（useAgentPublishRequest 返回值），
 * pendingRequest=null 表示「无待确认请求」（详见该 hook）。 */
export function makeAgentPublishRequest(
  overrides: Record<string, unknown> = {}
) {
  return {
    pendingRequest: null,
    resolvedNotice: null,
    confirming: false,
    confirm: vi.fn().mockResolvedValue(undefined),
    cancel: vi.fn().mockResolvedValue(undefined),
    clearNotice: vi.fn(),
    ...overrides,
  }
}
