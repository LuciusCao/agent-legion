import { describe, expect, it } from 'vitest'
import { extraQueryKeys } from './queryKeysExtra'

describe('extraQueryKeys', () => {
  it('keeps every public cache identity stable across domain rebalancing', () => {
    const filters = {
      groupBy: 'model',
      nodeKey: 'node-a',
      model: 'model-a',
      skillVersion: 'v1',
    }
    const actual = {
      users: extraQueryKeys.users(),
      workspace: extraQueryKeys.workspace('ws1'),
      workspaceMembers: extraQueryKeys.workspaceMembers('ws1'),
      workspaceMaterials: extraQueryKeys.workspaceMaterials('ws1'),
      workerTokens: extraQueryKeys.workerTokens(),
      workspaceApiTokens: extraQueryKeys.workspaceApiTokens('ws1'),
      workerConsole: extraQueryKeys.workerConsole(),
      workspaceWorkers: extraQueryKeys.workspaceWorkers('ws1'),
      workspaceRuntimeModels: extraQueryKeys.workspaceRuntimeModels('ws1'),
      tokenUsagePricing: extraQueryKeys.tokenUsagePricing(),
      instanceSettings: extraQueryKeys.instanceSettings(),
      skillDirectories: extraQueryKeys.skillDirectories('ws1'),
      studioAgents: extraQueryKeys.studioAgents(),
      connections: extraQueryKeys.connections(),
      connectionKeys: extraQueryKeys.connectionKeys(),
      infraConnections: extraQueryKeys.infraConnections(),
      connectionTypes: extraQueryKeys.connectionTypes(),
      workflowDefinition: extraQueryKeys.workflowDefinition('workflow-a'),
      workspaceSettings: extraQueryKeys.workspaceSettings('ws1'),
      failedNodeRuns: extraQueryKeys.failedNodeRuns('ws1'),
      workspaceTokenUsage: extraQueryKeys.workspaceTokenUsage('ws1', filters),
      jobTokenUsage: extraQueryKeys.jobTokenUsage('job-1'),
      runTokenUsage: extraQueryKeys.runTokenUsage('job-1', 7, 'running'),
      qualityBatches: extraQueryKeys.qualityBatches('ws1'),
      qualityBatchDetail: extraQueryKeys.qualityBatchDetail('ws1', 'batch-1'),
      qualityBatchStats: extraQueryKeys.qualityBatchStats('ws1', 'batch-1'),
      qualityItemDetail: extraQueryKeys.qualityItemDetail('ws1', 'item-1'),
      qualityReplays: extraQueryKeys.qualityReplays('ws1', 'item-1'),
      qualityReplayProfiles: extraQueryKeys.qualityReplayProfiles(
        'ws1',
        'item-1'
      ),
      qualityReplayDetail: extraQueryKeys.qualityReplayDetail(
        'ws1',
        'replay-1'
      ),
      studioAgentCatalog: extraQueryKeys.studioAgentCatalog('ws1'),
      studioSkillDetail: extraQueryKeys.studioSkillDetail(
        'group/skill',
        'ws1',
        'v2'
      ),
      nodeRuns: extraQueryKeys.nodeRuns('ws1', 'node-a', 'group/skill'),
      studioNodePromptPreview: extraQueryKeys.studioNodePromptPreview(
        'ws1',
        'node-a',
        'key: workflow\n'
      ),
      workflowStudioData: extraQueryKeys.workflowStudioData('ws1'),
      workflowStudioDraft: extraQueryKeys.workflowStudioDraft('ws1'),
      agentDefinitions: extraQueryKeys.agentDefinitions('ws1'),
      agentProvenance: extraQueryKeys.agentProvenance('ws1'),
      studioPublishRequest: extraQueryKeys.studioPublishRequest('ws1'),
      workspaceSharedMaterials: extraQueryKeys.workspaceSharedMaterials('ws1'),
    }

    expect(Object.keys(actual).sort()).toEqual(
      Object.keys(extraQueryKeys).sort()
    )
    expect(actual).toEqual({
      users: ['users'],
      workspace: ['workspace', 'ws1'],
      workspaceMembers: ['workspaceMembers', 'ws1'],
      workspaceMaterials: ['workspaceMaterials', 'ws1'],
      workerTokens: ['workerTokens'],
      workspaceApiTokens: ['workspaceApiTokens', 'ws1'],
      workerConsole: ['workerConsole'],
      workspaceWorkers: ['workspaceWorkers', 'ws1'],
      workspaceRuntimeModels: ['workspaceRuntimeModels', 'ws1'],
      tokenUsagePricing: ['tokenUsagePricing'],
      instanceSettings: ['instanceSettings'],
      skillDirectories: ['skillDirectories', 'ws1'],
      studioAgents: ['studioAgents'],
      connections: ['connections'],
      connectionKeys: ['connectionKeys'],
      infraConnections: ['infraConnections'],
      connectionTypes: ['connectionTypes'],
      workflowDefinition: ['workflowDefinition', 'workflow-a'],
      workspaceSettings: ['workspaceSettings', 'ws1'],
      failedNodeRuns: ['failedNodeRuns', 'ws1'],
      workspaceTokenUsage: ['workspaceTokenUsage', 'ws1', filters],
      jobTokenUsage: ['jobTokenUsage', 'job-1'],
      runTokenUsage: ['runTokenUsage', 'job-1', 7, 'running'],
      qualityBatches: ['qualityBatches', 'ws1'],
      qualityBatchDetail: ['qualityBatchDetail', 'ws1', 'batch-1'],
      qualityBatchStats: ['qualityBatchStats', 'ws1', 'batch-1'],
      qualityItemDetail: ['qualityItemDetail', 'ws1', 'item-1'],
      qualityReplays: ['qualityReplays', 'ws1', 'item-1'],
      qualityReplayProfiles: ['qualityReplayProfiles', 'ws1', 'item-1'],
      qualityReplayDetail: ['qualityReplayDetail', 'ws1', 'replay-1'],
      studioAgentCatalog: ['studioAgentCatalog', 'ws1'],
      studioSkillDetail: ['studioSkillDetail', 'group/skill', 'ws1', 'v2'],
      nodeRuns: ['nodeRuns', 'ws1', 'node-a', 'group/skill'],
      studioNodePromptPreview: [
        'studioNodePromptPreview',
        'ws1',
        'node-a',
        'key: workflow\n',
      ],
      workflowStudioData: ['workflowStudioData', 'ws1'],
      workflowStudioDraft: ['workflowStudioDraft', 'ws1'],
      agentDefinitions: ['agentDefinitions', 'ws1'],
      agentProvenance: ['agentProvenance', 'ws1'],
      studioPublishRequest: ['studioPublishRequest', 'ws1'],
      workspaceSharedMaterials: ['workspaceSharedMaterials', 'ws1'],
    })
  })
})
