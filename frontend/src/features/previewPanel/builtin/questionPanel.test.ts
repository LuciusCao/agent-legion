/**
 * 官方内置 question 预览面板 bundle 的行为测试（issue #328）。
 *
 * bundle 是单文件 HTML+内联脚本，运行在沙箱 iframe 里；这里用
 * runScripts:'dangerously' 的 JSDOM 真实执行它，win.parent === win
 * 的 jsdom 语义让「宿主」可以直接监听/回包 postMessage——桥协议、数据
 * 回落链、gating、交互与 LaTeX 降级都在这一层钉住。
 */
import { describe, it, expect } from 'vitest'
import { JSDOM } from 'jsdom'
import { QUESTION_PANEL_BUNDLE } from './questionPanelBundle'

const PANEL_SOURCE = 'agent-legion-preview-panel'
const HOST_SOURCE = 'agent-legion-preview-host'

interface FakeJob {
  detail: unknown
  /** name → 文件内容（JSON 字符串）；缺失的 name 回 ok:false。 */
  artifacts: Record<string, string>
}

function makeDetail(nodes: Array<Record<string, string>>) {
  return {
    job: { id: 'j1', status: 'completed', source_type: 'question' },
    nodes,
    runs: [],
    artifacts: [],
  }
}

const ALL_TERMINAL_NODES = [
  { node_key: 'generate_key_info', status: 'completed' },
  { node_key: 'generate_possible_errors', status: 'completed' },
  { node_key: 'review_key_info', status: 'completed' },
  { node_key: 'review_possible_errors', status: 'completed' },
]

const QUESTION = {
  stem: '<p>学校订了24箱牛奶，每箱 $10$ 盒，一共多少袋？</p>',
  options: [
    { label: 'A', content: '240' },
    { label: 'B', content: '3600' },
  ],
  answer: 'B',
  analysis_steps: [
    [
      { content: '<p>先求总盒数</p>', title: '<p>提示</p>', step: 0 },
      { content: '<p>24×10×15=3600</p>', title: '', step: 1 },
    ],
  ],
}

const KEY_INFO = {
  key_info_id: 'ki-1',
  type: 'given',
  content: { text: '每箱10盒', position: { start: 0, end: 1 } },
  question_comprehension_ability: '信息提取',
  question: {
    text: '每箱有几盒？',
    options: [{ label: 'A', text: '10盒', is_correct: true }],
  },
}

const POSSIBLE_ERROR = {
  error_id: 'pe-1',
  error_type: 'question_comprehension',
  position: 1,
  error_answer: ['240'],
  error_description: '把每箱10盒误读成每盒10袋',
  related_key_info_ids: ['ki-1'],
}

interface BootedPanel {
  win: Window
  doc: Document
  requests: Array<{ id: number; method: string; params?: { name?: string } }>
  /** documentElement.style.setProperty 的调用记录（jsdom 不存储自定义属性值）。 */
  themeCalls: Array<[string, string]>
}

function bootPanel(
  job: FakeJob,
  katexStub?: { renderToString: unknown }
): BootedPanel {
  const requests: BootedPanel['requests'] = []
  const themeCalls: BootedPanel['themeCalls'] = []

  function hostRespond(
    win: Window,
    message: { id: number; method: string; params?: { name?: string } }
  ) {
    requests.push(message)
    let ok = true
    let payload: unknown
    if (message.method === 'getJobDetail') {
      payload = job.detail
    } else if (message.method === 'listArtifacts') {
      payload = Object.keys(job.artifacts).sort()
    } else if (message.method === 'readArtifact') {
      const name = message.params?.name ?? ''
      if (name in job.artifacts) {
        payload = { name, content: job.artifacts[name] }
      } else {
        ok = false
      }
    } else {
      ok = false
    }
    win.postMessage(
      {
        source: HOST_SOURCE,
        type: 'response',
        id: message.id,
        ok,
        ...(ok ? { payload } : { error: 'not found' }),
      },
      '*'
    )
  }

  const dom = new JSDOM(QUESTION_PANEL_BUNDLE, {
    runScripts: 'dangerously',
    beforeParse(win) {
      if (katexStub) {
        ;(win as unknown as Record<string, unknown>).katex = katexStub
      }
      // 宿主侧监听器必须在面板脚本（构造期同步执行）之前挂上，否则错过 ready。
      win.addEventListener('message', (event) => {
        const data = event.data as Record<string, unknown> | null
        if (!data || data.source !== PANEL_SOURCE) return
        if (data.type === 'ready') {
          // jsdom 的 CSSOM 不保留自定义属性值（var(--pp-*) 写入后读不回），
          // 用探针记录 applyTheme 的调用。beforeParse 时 documentElement 尚未
          // 解析，探针只能在 ready（面板脚本已跑完）处安装。
          // （成员访问全部走局部变量：browserTestFilesGuard 按 document./
          // window. 静态特征判定 DOM 依赖，本测试自带 JSDOM、跑 node 工程。）
          const panelDoc = win.document
          const style = panelDoc.documentElement?.style
          const probed = style as
            | (CSSStyleDeclaration & { __ppProbed?: boolean })
            | undefined
          if (style && probed && !probed.__ppProbed) {
            probed.__ppProbed = true
            const original = style.setProperty.bind(style)
            style.setProperty = (name: string, value: string) => {
              themeCalls.push([name, value])
              original(name, value)
            }
          }
          win.postMessage(
            {
              source: HOST_SOURCE,
              type: 'init',
              jobId: 'j1',
              theme: { '--pp-bg': '#ff0000' },
              assets: {},
            },
            '*'
          )
        } else if (data.type === 'request') {
          hostRespond(win, data as unknown as { id: number; method: string })
        }
      })
    },
  })
  const { window: panelWindow } = dom
  return {
    win: panelWindow as unknown as Window,
    doc: panelWindow.document,
    requests,
    themeCalls,
  }
}

function appText(doc: Document): string {
  // 只看渲染产物：body.textContent 会带上 <script> 源码，污染断言。
  return doc.getElementById('app')?.textContent ?? ''
}

async function waitForText(doc: Document, text: string, present = true) {
  await expect
    .poll(() => appText(doc).includes(text), { timeout: 2000 })
    .toBe(present)
}

describe('官方内置 question 面板 bundle', () => {
  it('渲染题干/选项/答案/解析，并标记正确选项', async () => {
    const { doc } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
      },
    })

    await waitForText(doc, '学校订了24箱牛奶')
    // LaTeX 无 katex 资源时降级为原文片段
    await waitForText(doc, '10')
    expect(appText(doc)).toContain('选项')
    expect(appText(doc)).toContain('A.')
    expect(appText(doc)).toContain('3600')
    // answer: 'B' → B 选项带正确标记
    const correct = doc.querySelector('#app .option-item.correct')
    expect(correct?.textContent).toContain('B')
    expect(appText(doc)).toContain('答案')
    expect(appText(doc)).toContain('先求总盒数')
    expect(appText(doc)).toContain('24×10×15=3600')
  })

  it('富文本走白名单消毒：保留 p/strong/em/img(http)，剥除危险标签与属性', async () => {
    const { doc } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [
            {
              normalized: {
                stem:
                  '<p>学校订了<strong>24箱</strong>牛奶' +
                  '<script>alert(1)</script><em>哦</em>' +
                  '<img src="https://cdn.test/a.png">' +
                  '<img src="javascript:alert(1)" onerror="alert(2)">' +
                  '<a href="https://x.test">链接</a></p>',
              },
            },
          ],
        }),
      },
    })

    await waitForText(doc, '学校订了')
    const stem = doc.querySelector('#app .rich-text')!
    // 白名单标签保留
    expect(stem.querySelector('strong')?.textContent).toBe('24箱')
    expect(stem.querySelector('em')?.textContent).toBe('哦')
    // script 与 a 不在白名单：unwrap 后子文本保留为惰性文本，标签不落地
    expect(stem.querySelector('script')).toBeNull()
    expect(stem.querySelector('a')).toBeNull()
    expect(stem.textContent).toContain('alert(1)')
    expect(stem.textContent).toContain('链接')
    // img：http(s) src 保留并强制 no-referrer；javascript: src 整个剥除，事件属性不落地
    const imgs = stem.querySelectorAll('img')
    expect(imgs).toHaveLength(1)
    expect(imgs[0].getAttribute('src')).toBe('https://cdn.test/a.png')
    expect(imgs[0].getAttribute('referrerpolicy')).toBe('no-referrer')
    expect(imgs[0].getAttribute('onerror')).toBeNull()
  })

  it('纯文本提取走 inert DOMParser：onerror 载荷不执行、元素不进活动文档', async () => {
    const { doc, win } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        'comprehension_info.json': JSON.stringify({
          comprehension_data: {
            key_info_list: [
              {
                ...KEY_INFO,
                content: {
                  text: '每箱<img src="https://evil.test/x" onerror="window.__pwned=1">10盒',
                  position: { start: 0, end: 1 },
                },
              },
            ],
            possible_error_list: [],
          },
        }),
      },
    })

    await waitForText(doc, '审题信息')
    // stripTags（richInline 与题干高亮提取共用）经 inert DOMParser 解析：
    // 事件属性不触发，载荷元素也从不进入活动文档。详情卡展示完整文本。
    doc.querySelector<HTMLButtonElement>('#app .chip')!.click()
    await waitForText(doc, '每箱10盒')
    expect((win as unknown as Record<string, unknown>).__pwned).toBeUndefined()
    expect(doc.querySelector('#app img')).toBeNull()
  })

  it('选中审题信息 chip 时题干按匹配文本高亮', async () => {
    const keyInfo = {
      ...KEY_INFO,
      // 题干 plain 文本：'学校订了24箱牛奶，每箱 $10$ 盒，一共多少袋？'
      // '每箱 $10$' 位于 [10, 17)。
      content: { text: '每箱 $10$', position: { start: 10, end: 17 } },
    }
    const { doc } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        'comprehension_info.json': JSON.stringify({
          comprehension_data: {
            key_info_list: [keyInfo],
            possible_error_list: [],
          },
        }),
      },
    })

    await waitForText(doc, '审题信息')
    doc.querySelector<HTMLButtonElement>('#app .chip')!.click()
    const highlight = doc.querySelector('#app .rich-text .highlight')
    expect(highlight).not.toBeNull()
    // $10$ 按 LaTeX 段渲染（无 katex 资源时降级 span），定界符不落地
    expect(highlight!.textContent).toBe('每箱 10')
    expect(highlight!.querySelector('.latex-fallback')?.textContent).toBe('10')
    expect(highlight!.getAttribute('data-ids')).toBe('ki-1')
  })

  it('position 与目标文本不符时按最近出现处纠正并标记 corrected', async () => {
    const keyInfo = {
      ...KEY_INFO,
      content: { text: '每箱 $10$ 盒', position: { start: 0, end: 4 } },
    }
    const { doc } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        'comprehension_info.json': JSON.stringify({
          comprehension_data: {
            key_info_list: [keyInfo],
            possible_error_list: [],
          },
        }),
      },
    })

    await waitForText(doc, '审题信息')
    doc.querySelector<HTMLButtonElement>('#app .chip')!.click()
    const corrected = doc.querySelector('#app .rich-text .highlight-corrected')
    expect(corrected).not.toBeNull()
    expect(corrected!.textContent).toBe('每箱 10 盒')
  })

  it('应用宿主注入的主题变量', async () => {
    const { doc, themeCalls } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
      },
    })

    await waitForText(doc, '题干')
    expect(themeCalls).toContainEqual(['--pp-bg', '#ff0000'])
  })

  it('gate 未达时隐藏审题信息/易错点 section', async () => {
    const { doc } = bootPanel({
      detail: makeDetail([
        { node_key: 'generate_key_info', status: 'running' },
      ]),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        'comprehension_info.json': JSON.stringify({
          comprehension_data: {
            key_info_list: [KEY_INFO],
            possible_error_list: [POSSIBLE_ERROR],
          },
        }),
      },
    })

    await waitForText(doc, '题干')
    expect(appText(doc)).not.toContain('审题信息')
    expect(appText(doc)).not.toContain('常见审题错误')
  })

  it('gate 达成后渲染审题信息 chips，点击展开详情', async () => {
    const { doc } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        'comprehension_info.json': JSON.stringify({
          comprehension_data: {
            key_info_list: [KEY_INFO],
            possible_error_list: [POSSIBLE_ERROR],
          },
        }),
      },
    })

    await waitForText(doc, '审题信息')
    expect(appText(doc)).toContain('常见审题错误')

    const chip = doc.querySelector<HTMLButtonElement>('#app .chip')
    expect(chip?.textContent).toContain('每箱10盒')
    chip!.click()
    await waitForText(doc, '关联能力')
    expect(appText(doc)).toContain('信息提取')
    // 苏格拉底追问
    expect(appText(doc)).toContain('每箱有几盒？')
    // 关联错误互相引用
    expect(appText(doc)).toContain('把每箱10盒误读成每盒10袋')
  })

  it('评审报告徽标按 decision 渲染', async () => {
    const { doc } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        'comprehension_info.json': JSON.stringify({
          comprehension_data: {
            key_info_list: [KEY_INFO],
            possible_error_list: [POSSIBLE_ERROR],
          },
        }),
        'key_info_review_report.json': JSON.stringify({
          decisions: [
            { key_info_id: 'ki-1', decision: 'approved', reason: 'OK' },
          ],
        }),
        'possible_errors_review_report.json': JSON.stringify({
          decisions: [
            { error_id: 'pe-1', decision: 'rejected', reason: '不对' },
          ],
        }),
      },
    })

    await waitForText(doc, '已通过')
    expect(appText(doc)).toContain('已驳回')
  })

  it('评审节点未到终态时不拉取评审报告', async () => {
    const { doc, requests } = bootPanel({
      detail: makeDetail([
        { node_key: 'generate_key_info', status: 'completed' },
        { node_key: 'review_key_info', status: 'running' },
      ]),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        'comprehension_info.json': JSON.stringify({
          comprehension_data: {
            key_info_list: [KEY_INFO],
            possible_error_list: [],
          },
        }),
        'key_info_review_report.json': JSON.stringify({
          decisions: [{ key_info_id: 'ki-1', decision: 'approved' }],
        }),
      },
    })

    await waitForText(doc, '审题信息')
    const names = requests
      .filter((r) => r.method === 'readArtifact')
      .map((r) => r.params?.name)
    expect(names).not.toContain('key_info_review_report.json')
    expect(appText(doc)).not.toContain('已通过')
  })

  it('comprehension_info.json 缺失时回落 reviewed → raw 链', async () => {
    const { doc, requests } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
        // 无 comprehension_info.json，无 key_info_reviewed.json → 用 raw
        'key_info_raw.json': JSON.stringify({ key_info_list: [KEY_INFO] }),
        'possible_errors_reviewed.json': JSON.stringify({
          possible_error_list: [POSSIBLE_ERROR],
        }),
      },
    })

    await waitForText(doc, '审题信息')
    const names = requests
      .filter((r) => r.method === 'readArtifact')
      .map((r) => r.params?.name)
    expect(names).toContain('key_info_reviewed.json')
    expect(names).toContain('key_info_raw.json')
    expect(names.indexOf('key_info_reviewed.json')).toBeLessThan(
      names.indexOf('key_info_raw.json')
    )
    expect(appText(doc)).toContain('每箱10盒')
    expect(appText(doc)).toContain('常见审题错误')
  })

  it('LaTeX 在 katex 可用时渲染、不可用时降级原文', async () => {
    const withKatex = bootPanel(
      {
        detail: makeDetail(ALL_TERMINAL_NODES),
        artifacts: {
          'questions.json': JSON.stringify({
            questions: [{ normalized: QUESTION }],
          }),
        },
      },
      {
        renderToString: (latex: string) =>
          `<span class="math-rendered">[${latex}]</span>`,
      }
    )
    await waitForText(withKatex.doc, '题干')
    expect(
      withKatex.doc.querySelector('#app .math-rendered')?.textContent
    ).toBe('[10]')

    const withoutKatex = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {
        'questions.json': JSON.stringify({
          questions: [{ normalized: QUESTION }],
        }),
      },
    })
    await waitForText(withoutKatex.doc, '题干')
    expect(withoutKatex.doc.querySelector('#app .math-rendered')).toBeNull()
    expect(
      withoutKatex.doc.querySelector('#app .latex-fallback')?.textContent
    ).toBe('10')
  })

  it('无 questions.json 时渲染空态', async () => {
    const { doc } = bootPanel({
      detail: makeDetail(ALL_TERMINAL_NODES),
      artifacts: {},
    })
    await waitForText(doc, '题目数据尚未生成')
  })
})
