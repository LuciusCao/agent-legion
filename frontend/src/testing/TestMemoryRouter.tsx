import {
  MemoryRouter as ReactRouterMemoryRouter,
  type MemoryRouterProps,
} from 'react-router-dom'
import { TestQueryProvider } from './testQueryClient'

export function MemoryRouter(props: MemoryRouterProps) {
  return (
    <TestQueryProvider>
      <ReactRouterMemoryRouter
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        {...props}
      />
    </TestQueryProvider>
  )
}
