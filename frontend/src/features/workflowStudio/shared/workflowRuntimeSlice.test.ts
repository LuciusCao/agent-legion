import { describe, expect, it } from 'vitest'
import {
  combineYamlStructureRuntime,
  diffRuntimeSliceKeys,
  extractRuntimeSlice,
  hasStructuralYamlChanges,
  hasTopLevelExecution,
} from './workflowRuntimeSlice'

const BASELINE = `key: ws1
label: demo
execution:
  provider: openai
  model: gpt-5.1
nodes:
  _start:
    type: start
  write_script:
    type: agent
    label: 撰写脚本
    capability: write_script
    execution:
      runtime: velites
      model: gpt-5.1
      thinking: low
    tools: [web_search]
    requires_labels:
      gpu: a100
    inputs: [topic]
    outputs: [script_md]
  intake:
    type: code
    capability: intake
    after: [_start]
edges:
  - from: intake
    to: write_script
`

describe('hasTopLevelExecution', () => {
  it('检测顶层块而不误判节点 execution', () => {
    expect(hasTopLevelExecution(BASELINE)).toBe(true)
    expect(
      hasTopLevelExecution(
        BASELINE.replace(
          'execution:\n  provider: openai\n  model: gpt-5.1\n',
          ''
        )
      )
    ).toBe(false)
  })
})

describe('extractRuntimeSlice', () => {
  it('只归一化节点 runtime 键（含空值），忽略顶层 execution', () => {
    const slice = extractRuntimeSlice(BASELINE)
    expect(slice).toEqual({ nodes: expect.any(Object) })
    expect(slice.nodes.write_script).toEqual({
      provider: '',
      model: 'gpt-5.1',
      thinking: 'low',
      prompt: '',
      prompt_mode: '',
      tools: ['web_search'],
      requires_labels: { gpu: 'a100' },
    })
    // code 节点同样带完整空键集（与后端 extract_runtime_config 同形）
    expect(slice.nodes.intake).toEqual({
      provider: '',
      model: '',
      thinking: '',
      prompt: '',
      prompt_mode: '',
      tools: [],
      requires_labels: {},
    })
  })

  it('非字符串 junk 值按空处理', () => {
    const slice = extractRuntimeSlice(
      'key: ws1\nnodes:\n  a:\n    type: agent\n    execution:\n      model: 1\n    tools: nope\n'
    )
    expect(slice.nodes.a.model).toBe('')
    expect(slice.nodes.a.tools).toEqual([])
  })
})

describe('diffRuntimeSliceKeys', () => {
  it('只报节点级差异的点路径，顶层变化不进入运行通道', () => {
    const draft = BASELINE.replace(
      'model: gpt-5.1\n      thinking: low',
      'model: gpt-5.2\n      thinking: low'
    )
      .replace('provider: openai', 'provider: anthropic')
      .replace('tools: [web_search]', 'tools: [web_search, file_read]')
    const keys = diffRuntimeSliceKeys(
      extractRuntimeSlice(BASELINE),
      extractRuntimeSlice(draft)
    )
    expect(keys).toEqual([
      'nodes.write_script.model',
      'nodes.write_script.tools',
    ])
  })

  it('requires_labels 按标签逐键比对', () => {
    const draft = BASELINE.replace('gpu: a100', 'gpu: h20')
    expect(
      diffRuntimeSliceKeys(
        extractRuntimeSlice(BASELINE),
        extractRuntimeSlice(draft)
      )
    ).toEqual(['nodes.write_script.requires_labels.gpu'])
  })

  it('新增/删除的节点属结构改动，不计入 runtime diff', () => {
    const draft = BASELINE.replace(
      'edges:',
      '  extra:\n    type: agent\n    execution:\n      model: gpt-5.2\nedges:'
    )
    expect(
      diffRuntimeSliceKeys(
        extractRuntimeSlice(BASELINE),
        extractRuntimeSlice(draft)
      )
    ).toEqual([])
  })
})

describe('combineYamlStructureRuntime', () => {
  it('结构取基线、runtime 切片取草稿；execution.runtime 保留结构侧', () => {
    // 草稿：改了 label（结构）+ model（runtime）+ 新增 requires_labels 值变化
    const draft = BASELINE.replace(
      'label: 撰写脚本',
      'label: 撰写脚本（精修版）'
    )
      .replace(
        'model: gpt-5.1\n      thinking: low',
        'model: gpt-5.2\n      thinking: high'
      )
      .replace('gpu: a100', 'gpu: h20')
    const merged = combineYamlStructureRuntime(BASELINE, draft)
    const slice = extractRuntimeSlice(merged)
    expect(slice.nodes.write_script.model).toBe('gpt-5.2')
    expect(slice.nodes.write_script.thinking).toBe('high')
    expect(slice.nodes.write_script.requires_labels).toEqual({ gpu: 'h20' })
    // 结构字段来自基线（label 不跟草稿）
    expect(merged).toContain('label: 撰写脚本\n')
    expect(merged).not.toContain('精修版')
    // execution.runtime 是结构键，即使草稿改了也不跟
    const runtimeSwitched = draft.replace('runtime: velites', 'runtime: pi')
    expect(combineYamlStructureRuntime(BASELINE, runtimeSwitched)).toContain(
      'runtime: velites'
    )
  })

  it('runtime 侧空值 = 清除节点声明', () => {
    const draft = BASELINE.replace(
      '      model: gpt-5.1\n      thinking: low\n',
      ''
    )
      .replace('    tools: [web_search]\n', '')
      .replace('    requires_labels:\n      gpu: a100\n', '')
    const merged = combineYamlStructureRuntime(BASELINE, draft)
    const slice = extractRuntimeSlice(merged)
    expect(slice.nodes.write_script.model).toBe('')
    expect(slice.nodes.write_script.tools).toEqual([])
    expect(slice.nodes.write_script.requires_labels).toEqual({})
    expect(merged).not.toContain('requires_labels')
  })

  it('prompt 尾随换行原样保留（r1 观察①：与后端 apply_runtime_config 对齐）', () => {
    const draft = BASELINE.replace(
      '      thinking: low\n',
      '      thinking: low\n      prompt: |\n        第一行\n        第二行\n'
    )
    const merged = combineYamlStructureRuntime(BASELINE, draft)
    expect(extractRuntimeSlice(merged).nodes.write_script.prompt).toBe(
      '第一行\n第二行\n'
    )
  })

  it('顶层 execution 不搬值、不删除结构侧原文', () => {
    const draft = BASELINE.replace('provider: openai', 'provider: google')
    const merged = combineYamlStructureRuntime(BASELINE, draft)
    expect(merged).toContain('provider: openai')
    expect(merged).not.toContain('provider: google')
  })
})

describe('hasStructuralYamlChanges', () => {
  it('仅 runtime 差异 = 无结构改动', () => {
    const draft = BASELINE.replace(
      'model: gpt-5.1\n      thinking: low',
      'model: gpt-5.2\n      thinking: high'
    )
      .replace('gpu: a100', 'gpu: h20')
      .replace('provider: openai', 'provider: google')
    expect(hasStructuralYamlChanges(BASELINE, draft)).toBe(false)
  })

  it('label / capability / 边差异 = 结构改动', () => {
    expect(
      hasStructuralYamlChanges(
        BASELINE,
        BASELINE.replace('label: 撰写脚本', 'label: 改名')
      )
    ).toBe(true)
    expect(
      hasStructuralYamlChanges(
        BASELINE,
        BASELINE.replace('edges:', '  extra:\n    type: code\nedges:')
      )
    ).toBe(true)
  })

  it('节点 execution.runtime 切换 = 结构改动', () => {
    expect(
      hasStructuralYamlChanges(
        BASELINE,
        BASELINE.replace('runtime: velites', 'runtime: pi')
      )
    ).toBe(true)
  })

  it('顶层 execution.runtime 不换算成节点结构值', () => {
    const base = `key: ws1
execution:
  runtime: velites
nodes:
  _start:
    type: start
  write_script:
    type: agent
    capability: write_script
    after: [_start]
`
    expect(
      hasStructuralYamlChanges(
        base,
        base.replace('runtime: velites', 'runtime: pi')
      )
    ).toBe(false)
  })

  it('键序不同的同义 YAML 不误判', () => {
    const reordered = `label: demo
key: ws1
execution:
  model: gpt-5.1
  provider: openai
edges:
  - to: write_script
    from: intake
nodes:
  intake:
    capability: intake
    after: [_start]
    type: code
  _start:
    type: start
  write_script:
    capability: write_script
    label: 撰写脚本
    type: agent
    execution:
      thinking: low
      runtime: velites
      model: gpt-5.1
    requires_labels:
      gpu: a100
    tools: [web_search]
    outputs: [script_md]
    inputs: [topic]
`
    expect(hasStructuralYamlChanges(BASELINE, reordered)).toBe(false)
  })
})
