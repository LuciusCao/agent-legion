import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from '../../testing/TestMemoryRouter'
import { InfraConnectionsSection } from './InfraConnectionsSection'
import {
  getInfraConnections,
  testInfraConnection,
} from '../../api/infraConnections'
import type { InfraConnectionsResponse } from '../../api/infraConnections'

vi.mock('../../api/infraConnections', () => ({
  getInfraConnections: vi.fn(),
  testInfraConnection: vi.fn(),
}))

const data: InfraConnectionsResponse = {
  database: {
    engine: 'postgresql',
    host: 'db.internal',
    port: 5432,
    name: 'agent_legion',
    user: 'legion',
    password_set: true,
    masked_url: 'postgresql://legion:***@db.internal:5432/agent_legion',
  },
  storage: {
    configured: true,
    endpoint_url: 'http://rustfs:9000',
    public_endpoint_url: '',
    bucket: 'agent-legion-materials',
    region: 'us-east-1',
    credentials: 'static',
    reachable: true,
  },
}

function renderSection() {
  return render(
    <MemoryRouter>
      <InfraConnectionsSection />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getInfraConnections).mockResolvedValue(data)
})

describe('InfraConnectionsSection', () => {
  it('renders the masked database summary and storage details', async () => {
    renderSection()

    expect(
      await screen.findByText(
        'postgresql://legion:***@db.internal:5432/agent_legion'
      )
    ).toBeInTheDocument()
    expect(screen.getByText('db.internal:5432')).toBeInTheDocument()
    expect(screen.getByText('已设置（不回显）')).toBeInTheDocument()
    expect(screen.getByText('agent-legion-materials')).toBeInTheDocument()
    expect(screen.getByText('http://rustfs:9000')).toBeInTheDocument()
    expect(screen.getByText('静态凭据（已设置，不回显）')).toBeInTheDocument()
    expect(screen.getByText('正常')).toBeInTheDocument()
  })

  it('shows the unconfigured state when storage has no bucket', async () => {
    vi.mocked(getInfraConnections).mockResolvedValue({
      ...data,
      storage: {
        configured: false,
        endpoint_url: '',
        public_endpoint_url: '',
        bucket: '',
        region: '',
        credentials: 'unconfigured',
        reachable: false,
      },
    })

    renderSection()

    expect(await screen.findByText('未配置')).toBeInTheDocument()
    expect(
      screen.getByText(/AGENT_LEGION_S3_BUCKET 未设置/)
    ).toBeInTheDocument()
    expect(screen.queryByText('Bucket')).not.toBeInTheDocument()
  })

  it('shows the unreachable badge when storage probing fails', async () => {
    vi.mocked(getInfraConnections).mockResolvedValue({
      ...data,
      storage: { ...data.storage, reachable: false },
    })

    renderSection()

    expect(await screen.findByText('不可达')).toBeInTheDocument()
  })

  it('reports a successful database connectivity test', async () => {
    vi.mocked(testInfraConnection).mockResolvedValue({
      target: 'database',
      ok: true,
      reason: null,
    })

    renderSection()
    fireEvent.click(await screen.findByLabelText('测试数据库连接'))

    expect(await screen.findByText('数据库连接正常')).toBeInTheDocument()
    expect(testInfraConnection).toHaveBeenCalledWith('database')
  })

  it('relays the server reason when the storage test fails', async () => {
    vi.mocked(testInfraConnection).mockResolvedValue({
      target: 'storage',
      ok: false,
      reason: 'EndpointConnectionError: boom',
    })

    renderSection()
    fireEvent.click(await screen.findByLabelText('测试对象存储连接'))

    expect(
      await screen.findByText('对象存储连接失败：EndpointConnectionError: boom')
    ).toBeInTheDocument()
    expect(testInfraConnection).toHaveBeenCalledWith('storage')
  })

  it('shows the request error when the test call itself fails', async () => {
    vi.mocked(testInfraConnection).mockRejectedValue(new Error('HTTP 403'))

    renderSection()
    fireEvent.click(await screen.findByLabelText('测试数据库连接'))

    await waitFor(() => {
      expect(
        screen.getByText('数据库连接测试请求失败：HTTP 403')
      ).toBeInTheDocument()
    })
  })

  it('shows the load error when GET fails', async () => {
    vi.mocked(getInfraConnections).mockRejectedValue(new Error('HTTP 403'))

    renderSection()

    expect(await screen.findByRole('alert')).toHaveTextContent('HTTP 403')
  })
})
