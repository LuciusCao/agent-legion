import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { invalidateStudioTurnEndQueries } from './studioChatInvalidation'

// #1079（#440 P3b）：Agent 定义写工具自 P3 起不写库——turn 结束不再失效
// agent-definitions 缓存（原 #387 的 draft 回落解析已随 inspector 清理删除）。

describe('invalidateStudioTurnEndQueries', () => {
  it('invalidates workflow data, agent catalog and skill detail, not agent definitions', () => {
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    invalidateStudioTurnEndQueries(queryClient, 'ws1')

    expect(spy).toHaveBeenCalledWith({
      queryKey: extraQueryKeys.workflowStudioData('ws1'),
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: extraQueryKeys.studioAgentCatalog('ws1'),
    })
    expect(spy).not.toHaveBeenCalledWith({
      queryKey: extraQueryKeys.agentDefinitions('ws1'),
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: ['studioSkillDetail'] })
  })
})
