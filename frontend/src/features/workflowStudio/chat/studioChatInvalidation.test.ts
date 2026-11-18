import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { extraQueryKeys } from '../../../lib/queryKeysExtra'
import { invalidateStudioTurnEndQueries } from './studioChatInvalidation'

describe('invalidateStudioTurnEndQueries', () => {
  it('invalidates workflow data, agent catalog and skill detail', () => {
    const queryClient = new QueryClient()
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    invalidateStudioTurnEndQueries(queryClient, 'ws1')

    expect(spy).toHaveBeenCalledWith({
      queryKey: extraQueryKeys.workflowStudioData('ws1'),
    })
    expect(spy).toHaveBeenCalledWith({
      queryKey: extraQueryKeys.studioAgentCatalog('ws1'),
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: ['studioSkillDetail'] })
  })
})
