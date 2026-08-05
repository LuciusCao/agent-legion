import { useState, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// 每个组件树独立 QueryClient：关闭重试与缓存，避免跨用例污染。
export function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
}

export function TestQueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(createTestQueryClient)
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}
