import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { JobAllMatchingUpgradeDialog } from './JobAllMatchingUpgradeDialog'

function renderDialog(onConfirm: (mode: 'clean' | 'inherit') => Promise<void>) {
  const onClose = vi.fn()
  render(
    <JobAllMatchingUpgradeDialog
      open
      count={3}
      onClose={onClose}
      onConfirm={onConfirm}
    />
  )
  return { onClose }
}

function confirm() {
  fireEvent.click(screen.getByRole('button', { name: '确认升级' }))
}

describe('JobAllMatchingUpgradeDialog', () => {
  it('closes after a successful confirm', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const { onClose } = renderDialog(onConfirm)

    confirm()

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(onConfirm).toHaveBeenCalledWith('clean')
  })

  it('keeps the dialog and the selected mode after a failed confirm (no unhandled rejection)', async () => {
    // #759 P1: a failed batch upgrade (e.g. a 500 after a partial commit)
    // must not close the dialog — the user retries with the chosen mode.
    const unhandled: unknown[] = []
    const onUnhandled = (reason: unknown) => {
      unhandled.push(reason)
    }
    process.on('unhandledRejection', onUnhandled)
    try {
      const onConfirm = vi
        .fn<(mode: 'clean' | 'inherit') => Promise<void>>()
        .mockRejectedValueOnce(new Error('boom'))
        .mockResolvedValue(undefined)
      const { onClose } = renderDialog(onConfirm)

      fireEvent.click(screen.getByRole('radio', { name: /继承未变节点产物/ }))
      confirm()

      await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1))
      expect(onConfirm).toHaveBeenLastCalledWith('inherit')
      expect(onClose).not.toHaveBeenCalled()
      // Dialog stays open with the mode selection intact for the retry.
      expect(screen.getByText('确认升级 workflow')).toBeTruthy()
      expect(
        screen.getByRole('radio', { name: /继承未变节点产物/ })
      ).toBeChecked()
      // The button is clickable again once the failed request settles.
      await waitFor(() =>
        expect(screen.getByRole('button', { name: '确认升级' })).toBeEnabled()
      )
      // handleConfirm caught the rejection: no unhandled rejection escaped.
      expect(unhandled).toEqual([])

      // Retry succeeds → dialog closes.
      confirm()
      await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
      expect(onConfirm).toHaveBeenLastCalledWith('inherit')
    } finally {
      process.removeListener('unhandledRejection', onUnhandled)
    }
  })
})
