import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { invalidateStudioTurnEndQueries } from './studioChatInvalidation'

// #387：MCP 的 save_agent_definition_draft 新建 draft-only Agent 后，turn
// 结束要失效 agent-definitions 缓存，节点详情的 draft 回落解析才能看到它。

describe('invalidateStudioTurnEndQueries', () => {
  it('invalidates workflow data, agent catalog, agent definitions, and skill detail', () => {
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    invalidateStudioTurnEndQueries(queryClient, 'ws1')

    expect(spy).toHaveBeenCalledWith({
      queryKey: extraQueryKeys.workflowStudioData('ws1'),
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: extraQueryKeys.studioAgentCatalog('ws1'),
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: extraQueryKeys.agentDefinitions('ws1'),
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: ['studioSkillDetail'] })
  })
})
