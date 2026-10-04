import { describe, expect, it } from 'vitest'
import { buildCurlExample, buildPythonExample } from './apiAccessSnippets'

const INPUT = { apiBase: 'https://legion.example.com', workspaceId: 'ws-1' }

function lines(text: string): string[] {
  return text.split('\n')
}

describe('buildCurlExample download step (#907)', () => {
  const curl = buildCurlExample(INPUT)
  const rows = lines(curl)

  it('falls back to raw only when the direct download is absent or fails', () => {
    const branch = rows.findIndex((row) =>
      row.startsWith('if [ -z "$DOWNLOAD_URL" ] || ! curl ')
    )
    expect(branch).toBeGreaterThan(-1)
    expect(rows[branch]).toContain('"$DOWNLOAD_URL"; then')
    const raw = rows.findIndex((row) => row.includes('/raw"'))
    const end = rows.findIndex((row, index) => index > branch && row === 'fi')
    // raw 请求只在 then 分支里：直连成功即不再请求 raw。
    expect(raw).toBeGreaterThan(branch)
    expect(end).toBeGreaterThan(raw)
    // 不再有无条件执行的直连 curl。
    expect(
      rows.filter(
        (row) => row.includes('"$DOWNLOAD_URL"') && row.startsWith('curl ')
      )
    ).toEqual([])
  })

  it('percent-encodes the artifact name before building the raw URL', () => {
    const encode = rows.find((row) => row.startsWith('ENCODED_NAME=$('))
    expect(encode).toBeDefined()
    expect(encode).toContain('urllib.parse.quote(sys.argv[1], safe="")')
    expect(encode).toContain('"$ARTIFACT_NAME"')
    const raw = rows.find((row) => row.includes('/raw"'))
    expect(raw).toContain('/artifacts/$ENCODED_NAME/raw"')
    expect(raw).not.toContain('$ARTIFACT_NAME')
  })
})

describe('buildPythonExample duplicate submission (#907)', () => {
  const python = buildPythonExample(INPUT)

  it('recognises the already-exists 400 before raising for status', () => {
    const check = python.indexOf(
      'if resp.status_code == 400 and resp.json().get("detail") == ALREADY_EXISTS:'
    )
    const raise = python.indexOf('resp.raise_for_status()')
    expect(check).toBeGreaterThan(-1)
    expect(raise).toBeGreaterThan(check)
    expect(python).toContain(
      'ALREADY_EXISTS = "No tasks were resolved from input"'
    )
  })

  it('reconciles the existing job by the item client_token', () => {
    expect(python).toContain('"client_token": CLIENT_TOKEN')
    expect(python).toContain('/jobs/snapshot"')
    expect(python).toContain('job["client_token"] == CLIENT_TOKEN')
    expect(python).toContain('cursor = page["next_cursor"]')
  })

  it('raises instead of picking the first match when a token is reused (#910)', () => {
    const loop = python.slice(
      python.indexOf('while not job_ids:'),
      python.indexOf('if len(matches) > 1:')
    )
    // 翻完全部页只累积命中，不在循环里提前收敛到某一个。
    expect(loop).toContain('matches += [')
    expect(loop).not.toContain('job_ids =')
    const verdict = python.slice(python.indexOf('if len(matches) > 1:'))
    const raise = verdict.indexOf('raise SystemExit(')
    const adopt = verdict.indexOf('job_ids = job_ids or matches')
    expect(raise).toBeGreaterThan(-1)
    expect(adopt).toBeGreaterThan(raise)
    expect(verdict.indexOf('job_id = job_ids[0]')).toBeGreaterThan(adopt)
  })

  it('backs off on 429 during reconciliation before reading the page', () => {
    const loop = python.slice(
      python.indexOf('while not job_ids:'),
      python.indexOf('cursor = page["next_cursor"]')
    )
    const steps = [
      '/jobs/snapshot',
      'if r.status_code == 429:',
      'time.sleep(int(r.headers.get("Retry-After"',
      'continue',
      'r.raise_for_status()',
      'page = r.json()',
      'page["jobs"]',
    ].map((step) => loop.indexOf(step))
    expect(steps.every((position) => position > -1)).toBe(true)
    expect(steps).toEqual([...steps].sort((a, b) => a - b))
    // run_id 读回失败（含 429）不解析错误体，落到去重键对账。
    expect(python).toContain('if readback.ok else []')
  })
})
