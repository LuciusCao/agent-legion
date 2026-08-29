import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { BasicInfoSection } from './BasicInfoSection'

describe('BasicInfoSection', () => {
  const mockNameChange = vi.fn()
  const mockDescriptionChange = vi.fn()
  const mockEntityTypeChange = vi.fn()

  beforeEach(() => {
    mockNameChange.mockReset()
    mockDescriptionChange.mockReset()
    mockEntityTypeChange.mockReset()
  })

  function renderSection(saveError: string | null = null) {
    return render(
      <BasicInfoSection
        workspaceName="Test Workspace"
        workspaceDescription="描述"
        entityType="question"
        saveError={saveError}
        onNameChange={mockNameChange}
        onDescriptionChange={mockDescriptionChange}
        onEntityTypeChange={mockEntityTypeChange}
      />
    )
  }

  it('edits the workspace name', () => {
    renderSection()

    fireEvent.change(screen.getByLabelText('Workspace 名称'), {
      target: { value: 'New Name' },
    })

    expect(mockNameChange).toHaveBeenCalledWith('New Name')
  })

  it('changes entity type', async () => {
    renderSection()

    const select = screen.getByRole('combobox', { name: '默认实体类型' })
    await act(async () => {
      fireEvent.mouseDown(select)
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('option', { name: 'knowledge' }))
    })

    expect(mockEntityTypeChange).toHaveBeenCalledWith('knowledge')
  })

  it('shows save error when provided', () => {
    renderSection('保存失败')

    expect(screen.getByText('保存失败')).toBeInTheDocument()
  })
})
