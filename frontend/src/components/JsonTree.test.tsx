import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { JsonTree } from './JsonTree'

describe('JsonTree', () => {
  it('renders primitive root value', () => {
    render(<JsonTree data="hello" />)
    expect(screen.getByText('"hello"')).toBeInTheDocument()
  })

  it('renders object keys and values', () => {
    render(<JsonTree data={{ name: 'alice', age: 30 }} />)
    expect(screen.getByText('name')).toBeInTheDocument()
    expect(screen.getByText('"alice"')).toBeInTheDocument()
    expect(screen.getByText('age')).toBeInTheDocument()
    expect(screen.getByText('30')).toBeInTheDocument()
  })

  it('renders array items', () => {
    render(<JsonTree data={['a', 'b']} />)
    expect(screen.getByText('"a"')).toBeInTheDocument()
    expect(screen.getByText('"b"')).toBeInTheDocument()
  })

  it('collapses all nodes when "全部折叠" is clicked', () => {
    render(<JsonTree data={{ a: { b: { c: 1 } } }} />)

    fireEvent.click(screen.getByText('全部展开'))
    expect(screen.getByText('b')).toBeInTheDocument()
    expect(screen.getByText('c')).toBeInTheDocument()

    fireEvent.click(screen.getByText('全部折叠'))

    expect(screen.queryByText('b')).not.toBeInTheDocument()
    expect(screen.queryByText('c')).not.toBeInTheDocument()
  })

  it('defaults to 2 collapsed levels for deep payloads', () => {
    // Large artifact trees must not mount their whole DOM at once; the root
    // object and its children stay visible, deeper levels collapse.
    render(<JsonTree data={{ a: { b: { c: 1 } } }} />)

    expect(screen.getByText('a')).toBeInTheDocument()
    expect(screen.getByText('b')).toBeInTheDocument()
    expect(screen.queryByText('c')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('全部展开'))
    expect(screen.getByText('c')).toBeInTheDocument()
  })

  it('expands all nodes when "全部展开" is clicked', () => {
    render(<JsonTree data={{ a: { b: { c: 1 } } }} />)

    fireEvent.click(screen.getByText('全部折叠'))
    expect(screen.queryByText('c')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('全部展开'))
    expect(screen.getByText('b')).toBeInTheDocument()
    expect(screen.getByText('c')).toBeInTheDocument()
  })
})
