import { describe, it, expect } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { makeJob } from '../../testing/fixtures'
import { pageSubtitle } from '../../pages/jobDetail/jobDetailTitle'
import { JobClientTokenSource } from './JobClientTokenSource'
import { JobListItemDescription } from './JobListItemDescription'

const MATERIAL_ID = '0123456789abcdef0123456789abcdef'

const tokenJob = makeJob({
  source_type: 'material',
  source_id: `${MATERIAL_ID}~order-1001`,
  source_base_id: MATERIAL_ID,
  client_token: 'order-1001',
  workflow_version: null,
})

const plainJob = makeJob({
  source_type: 'material',
  source_id: MATERIAL_ID,
  source_base_id: MATERIAL_ID,
  client_token: null,
  workflow_version: null,
})

describe('JobClientTokenSource (#925)', () => {
  it('shows source material and client_token with an explanatory tooltip', async () => {
    render(<JobClientTokenSource job={tokenJob} />)

    expect(screen.getByText(`来源材料：${MATERIAL_ID}`)).toBeInTheDocument()
    const token = screen.getByText('client_token：order-1001')
    fireEvent.mouseOver(token)
    expect(
      await screen.findByText(/以不同 client_token 提交会生成独立 job/)
    ).toBeInTheDocument()
  })

  it('labels bundle sources as folders', () => {
    render(
      <JobClientTokenSource
        job={{ ...tokenJob, source_type: 'bundle', source_id: 'b1~t' }}
      />
    )
    expect(screen.getByText(`来源文件夹：${MATERIAL_ID}`)).toBeInTheDocument()
  })

  it('renders nothing without a client_token', () => {
    const { container } = render(<JobClientTokenSource job={plainJob} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('job detail subtitle (#925)', () => {
  it('replaces the raw scoped source_id with material + client_token', () => {
    render(<>{pageSubtitle(tokenJob)}</>)

    expect(screen.getByText(`来源材料：${MATERIAL_ID}`)).toBeInTheDocument()
    expect(screen.getByText('client_token：order-1001')).toBeInTheDocument()
    expect(screen.queryByText(`${MATERIAL_ID}~order-1001`)).toBeNull()
  })

  it('keeps the plain source_id subtitle when the job has no token', () => {
    const { container } = render(<>{pageSubtitle(plainJob)}</>)

    expect(container).toHaveTextContent(new RegExp(`^${MATERIAL_ID}$`))
    expect(screen.queryByText(/client_token/)).toBeNull()
  })
})

describe('job list description (#925)', () => {
  it('surfaces the client_token only as a hover title', () => {
    render(<JobListItemDescription job={tokenJob} />)

    const sourceId = screen.getByText(`${MATERIAL_ID}~order-1001`)
    expect(sourceId).toHaveAttribute(
      'title',
      expect.stringContaining('client_token：order-1001')
    )
  })

  it('leaves token-less rows unchanged', () => {
    const { container } = render(<JobListItemDescription job={plainJob} />)

    expect(container.querySelector('[title]')).toBeNull()
    expect(container).toHaveTextContent(MATERIAL_ID)
  })
})
