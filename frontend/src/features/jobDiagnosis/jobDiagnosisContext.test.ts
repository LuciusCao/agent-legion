import { describe, expect, it } from 'vitest'
import {
  buildDiagnosisPrimer,
  latestJobActionSuggestions,
  parseJobActionSuggestions,
  suggestionKey,
} from './jobDiagnosisContext'
import type { ChatMessage } from '../workflowStudio/chat/studioChatMessages'

function agentText(id: string, seq: number, text: string): ChatMessage {
  return {
    id,
    session_id: 's1',
    kind: 'text',
    role: 'agent',
    content: { text },
    seq,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('buildDiagnosisPrimer', () => {
  it('carries the workspace + job + node binding and the tool map', () => {
    const primer = buildDiagnosisPrimer({
      workspaceId: 'ws-1',
      jobId: 'job-9',
      jobTitle: '代数题',
      nodeKey: 'write_script',
      nodeLabel: '撰写脚本',
    })
    expect(primer).toContain('workspace_id: ws-1')
    expect(primer).toContain('job_id: job-9')
    expect(primer).toContain('关注节点: write_script')
    // agent 需要知道观测工具集（authoring bootstrap 的工具清单不含它们）。
    for (const tool of [
      'get_job_context',
      'get_job_detail',
      'get_node_logs',
      'read_artifact',
      'list_jobs',
      'compare_jobs',
    ]) {
      expect(primer).toContain(tool)
    }
    // 动作面约定：只读 + 建议块格式。
    expect(primer).toContain('job_action_suggestion')
    expect(primer).toContain('"job_id": "job-9"')
  })

  it('omits the node line when no node is bound (list entry)', () => {
    const primer = buildDiagnosisPrimer({ workspaceId: 'ws-1', jobId: 'job-1' })
    expect(primer).not.toContain('关注节点')
  })
})

describe('parseJobActionSuggestions', () => {
  it('parses the fenced json envelope', () => {
    const text = [
      '分析结论：节点超时。',
      '```json',
      '{"job_action_suggestion": {"action": "rerun_node", "job_id": "job-1", "node_key": "write_script", "reason": "超时重试即可"}}',
      '```',
    ].join('\n')
    expect(parseJobActionSuggestions(text)).toEqual([
      {
        action: 'rerun_node',
        jobId: 'job-1',
        nodeKey: 'write_script',
        reason: '超时重试即可',
      },
    ])
  })

  it('ignores malformed blocks and unknown actions', () => {
    const text = [
      '```json',
      '{"job_action_suggestion": {"action": "delete_job", "job_id": "j", "node_key": "n"}}',
      '```',
      '```json',
      '{not json}',
      '```',
      '```json',
      '{"job_action_suggestion": {"action": "rerun_node", "job_id": "", "node_key": "n"}}',
      '```',
      '```json',
      '{"unrelated": true}',
      '```',
    ].join('\n')
    expect(parseJobActionSuggestions(text)).toEqual([])
  })

  it('accepts run_to_node and defaults the reason', () => {
    const text =
      '```json\n{"job_action_suggestion": {"action": "run_to_node", "job_id": "j1", "node_key": "review"}}\n```'
    expect(parseJobActionSuggestions(text)).toEqual([
      { action: 'run_to_node', jobId: 'j1', nodeKey: 'review', reason: '' },
    ])
  })
})

describe('latestJobActionSuggestions', () => {
  it('takes the last agent message with valid suggestions', () => {
    const messages = [
      agentText(
        'm1',
        1,
        '```json\n{"job_action_suggestion": {"action": "rerun_node", "job_id": "job-1", "node_key": "a"}}\n```'
      ),
      agentText('m2', 2, '再想想……'),
      agentText(
        'm3',
        3,
        '```json\n{"job_action_suggestion": {"action": "run_to_node", "job_id": "job-1", "node_key": "b"}}\n```'
      ),
    ]
    const found = latestJobActionSuggestions(messages, 'job-1')
    expect(found).toHaveLength(1)
    expect(found[0].nodeKey).toBe('b')
  })

  it('ignores suggestions for other jobs and user messages', () => {
    const suggestion =
      '```json\n{"job_action_suggestion": {"action": "rerun_node", "job_id": "job-OTHER", "node_key": "a"}}\n```'
    const messages = [
      agentText('m1', 1, suggestion),
      { ...agentText('m2', 2, suggestion), role: 'user' as const },
    ]
    expect(latestJobActionSuggestions(messages, 'job-1')).toEqual([])
  })

  it('returns empty when nothing suggests an action', () => {
    expect(
      latestJobActionSuggestions([agentText('m1', 1, '没有问题')], 'j')
    ).toEqual([])
  })
})

describe('suggestionKey', () => {
  it('is stable per action/job/node triple', () => {
    const key = suggestionKey({
      action: 'rerun_node',
      jobId: 'j',
      nodeKey: 'n',
      reason: 'x',
    })
    expect(key).toBe('rerun_node:j:n')
  })
})
