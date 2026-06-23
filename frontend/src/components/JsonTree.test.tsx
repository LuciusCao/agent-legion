import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { JsonTree } from './JsonTree'
import styles from './JsonTree.module.css'

describe('JsonTree', () => {
  it('renders primitive root value', () => {
    render(<JsonTree data="hello" />)
    expect(screen.getByText('"hello"')).toBeInTheDocument()
  })

  it('renders object keys and values by default', () => {
    render(<JsonTree data={{ name: 'alice', age: 30 }} />)
    expect(screen.getByText('name')).toBeInTheDocument()
    expect(screen.getByText('"alice"')).toBeInTheDocument()
    expect(screen.getByText('age')).toBeInTheDocument()
    expect(screen.getByText('30')).toBeInTheDocument()
  })

  it('renders array items without index keys', () => {
    render(<JsonTree data={['a', 'b']} />)
    const content = document.querySelector(`.${styles.content}`)
    expect(content).toBeInTheDocument()
    const contentText = content?.textContent || ''
    expect(contentText).not.toContain('0:')
    expect(contentText).not.toContain('1:')
    expect(screen.getByText('"a"')).toBeInTheDocument()
    expect(screen.getByText('"b"')).toBeInTheDocument()
  })

  it('renders line numbers', () => {
    render(<JsonTree data={{ a: 1 }} />)
    const lineNumbers = document.querySelectorAll(`.${styles.lineNumber}`)
    expect(lineNumbers).toHaveLength(3)
    expect(Array.from(lineNumbers).map((n) => n.textContent)).toEqual([
      '1',
      '2',
      '3',
    ])
  })

  it('collapses an object node when toggle is clicked', () => {
    render(<JsonTree data={{ outer: { inner: 'value' } }} />)

    expect(screen.getByText('inner')).toBeInTheDocument()

    const toggleButtons = screen.getAllByRole('button', { name: '折叠' })
    expect(toggleButtons.length).toBeGreaterThanOrEqual(2)

    fireEvent.click(toggleButtons[1])

    expect(screen.queryByText('inner')).not.toBeInTheDocument()
    expect(screen.getByText('{...}')).toBeInTheDocument()
  })

  it('expands a collapsed node when toggle is clicked again', () => {
    render(<JsonTree data={{ outer: { inner: 'value' } }} />)

    const toggleButtons = screen.getAllByRole('button', { name: '折叠' })
    fireEvent.click(toggleButtons[1])
    expect(screen.queryByText('inner')).not.toBeInTheDocument()

    const expandButtons = screen.getAllByRole('button', { name: '展开' })
    fireEvent.click(expandButtons[0])

    expect(screen.getByText('inner')).toBeInTheDocument()
  })

  it('collapses all nodes when "全部折叠" is clicked', () => {
    render(<JsonTree data={{ a: { b: { c: 1 } } }} />)

    expect(screen.getByText('b')).toBeInTheDocument()
    expect(screen.getByText('c')).toBeInTheDocument()

    fireEvent.click(screen.getByText('全部折叠'))

    expect(screen.queryByText('b')).not.toBeInTheDocument()
    expect(screen.queryByText('c')).not.toBeInTheDocument()
  })

  it('expands all nodes when "全部展开" is clicked', () => {
    render(<JsonTree data={{ a: { b: { c: 1 } } }} />)

    fireEvent.click(screen.getByText('全部折叠'))
    expect(screen.queryByText('c')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('全部展开'))
    expect(screen.getByText('c')).toBeInTheDocument()
  })

  it('shows item count on collapsed nodes', () => {
    render(<JsonTree data={{ outer: { a: 1, b: 2 } }} />)

    const toggleButtons = screen.getAllByRole('button', { name: '折叠' })
    fireEvent.click(toggleButtons[1])

    expect(screen.getByText('// 2 items')).toBeInTheDocument()
  })
})
