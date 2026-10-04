import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { JobDetailToolbar } from './JobDetailToolbar'

afterEach(() => vi.unstubAllGlobals())

describe('responsive job toolbar', () => {
  it.each([1440, 1000, 600])(
    'keeps every action accessible at %i px',
    (width) => {
      vi.stubGlobal('matchMedia', (query: string) => ({
        matches: width <= Number(query.match(/max-width:([\d.]+)/)?.[1] ?? 0),
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }))
      const execute = vi.fn()
      const remove = vi.fn()
      render(
        <JobDetailToolbar
          execution={[{ icon: 'restart_alt', label: '重跑', onClick: execute }]}
          secondary={[
            { icon: 'delete', label: '删除', color: 'error', onClick: remove },
          ]}
          onOpenDiagnosis={vi.fn()}
        />
      )
      const toolbar = screen.getByTestId('job-detail-actions')
      expect(within(toolbar).queryByLabelText('删除')).toBeNull()
      expect(screen.getByLabelText('排查助手')).toBeInTheDocument()
      if (width >= 760) {
        const button = screen.getByRole('button', { name: '重跑' })
        if (width < 1100) expect(button).not.toHaveTextContent('重跑')
        else expect(button).toHaveTextContent('重跑')
        fireEvent.click(button)
        expect(execute).toHaveBeenCalledOnce()
      } else expect(within(toolbar).queryByLabelText('重跑')).toBeNull()
      fireEvent.click(screen.getByRole('button', { name: '更多任务操作' }))
      const menu = screen.getByRole('menu', { name: '任务操作' })
      expect(
        within(menu).getByRole('menuitem', { name: '删除' })
      ).toBeInTheDocument()
      if (width < 760) {
        fireEvent.click(within(menu).getByRole('menuitem', { name: '重跑' }))
        expect(execute).toHaveBeenCalledOnce()
        expect(remove).not.toHaveBeenCalled()
      } else {
        fireEvent.click(within(menu).getByRole('menuitem', { name: '删除' }))
        expect(remove).toHaveBeenCalledOnce()
      }
    }
  )
})
