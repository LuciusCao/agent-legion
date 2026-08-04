import {
  MemoryRouter as ReactRouterMemoryRouter,
  type MemoryRouterProps,
} from 'react-router-dom'

export function MemoryRouter(props: MemoryRouterProps) {
  return (
    <ReactRouterMemoryRouter
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      {...props}
    />
  )
}
