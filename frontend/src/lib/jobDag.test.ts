import { describe, it, expect } from 'vitest'
import {
  ancestorClosure,
  isAncestor,
  validateRunTo,
  type DagNode,
} from './jobDag'

function makeNode(key: string, after: string[] = [], label = key): DagNode {
  return {
    key,
    label,
    after,
  }
}

describe('jobDag', () => {
  describe('ancestorClosure', () => {
    it('returns the target and all transitive ancestors in a linear workflow', () => {
      const nodes = [
        makeNode('a'),
        makeNode('b', ['a']),
        makeNode('c', ['b']),
        makeNode('d', ['c']),
      ]
      const closure = ancestorClosure(nodes, 'c')
      expect(closure).toContain('a')
      expect(closure).toContain('b')
      expect(closure).toContain('c')
      expect(closure).not.toContain('d')
    })

    it('collects ancestors across branches', () => {
      const nodes = [
        makeNode('a'),
        makeNode('b'),
        makeNode('c', ['a', 'b']),
        makeNode('d', ['c']),
        makeNode('e', ['b']),
      ]
      const closure = ancestorClosure(nodes, 'd')
      expect(closure).toContain('a')
      expect(closure).toContain('b')
      expect(closure).toContain('c')
      expect(closure).toContain('d')
      expect(closure).not.toContain('e')
    })

    it('returns only the target when it has no ancestors', () => {
      const nodes = [makeNode('root'), makeNode('leaf', ['root'])]
      expect(ancestorClosure(nodes, 'root')).toEqual(['root'])
    })

    it('returns an empty array for an unknown target', () => {
      const nodes = [makeNode('a')]
      expect(ancestorClosure(nodes, 'missing')).toEqual([])
    })

    it('ignores references to unknown ancestors', () => {
      const nodes = [makeNode('a', ['ghost'])]
      expect(ancestorClosure(nodes, 'a')).toEqual(['a'])
    })
  })

  describe('isAncestor', () => {
    it('is true for direct ancestors', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a'])]
      expect(isAncestor(nodes, 'b', 'a')).toBe(true)
    })

    it('is true for transitive ancestors', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a']), makeNode('c', ['b'])]
      expect(isAncestor(nodes, 'c', 'a')).toBe(true)
    })

    it('is false for siblings', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a']), makeNode('c', ['a'])]
      expect(isAncestor(nodes, 'c', 'b')).toBe(false)
    })

    it('is false for descendants', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a'])]
      expect(isAncestor(nodes, 'a', 'b')).toBe(false)
    })

    it('is false when either node is unknown', () => {
      const nodes = [makeNode('a')]
      expect(isAncestor(nodes, 'missing', 'a')).toBe(false)
      expect(isAncestor(nodes, 'a', 'missing')).toBe(false)
    })
  })

  describe('validateRunTo', () => {
    it('approves a target-only run-to', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a'])]
      expect(validateRunTo(nodes, 'b')).toEqual({ valid: true })
    })

    it('approves a start node inside the target closure', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a']), makeNode('c', ['b'])]
      expect(validateRunTo(nodes, 'c', 'b')).toEqual({ valid: true })
    })

    it('rejects a start node outside the target closure', () => {
      const nodes = [makeNode('a'), makeNode('b'), makeNode('c', ['a'])]
      const result = validateRunTo(nodes, 'c', 'b')
      expect(result.valid).toBe(false)
      expect(result.message).toContain('不在目标节点')
    })

    it('rejects an unknown target', () => {
      const nodes = [makeNode('a')]
      const result = validateRunTo(nodes, 'missing')
      expect(result.valid).toBe(false)
      expect(result.message).toContain('目标节点')
    })

    it('rejects an unknown start node', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a'])]
      const result = validateRunTo(nodes, 'b', 'missing')
      expect(result.valid).toBe(false)
      expect(result.message).toContain('起始节点')
    })

    it('rejects a start node equal to the target when a rerun-to is expected', () => {
      const nodes = [makeNode('a'), makeNode('b', ['a'])]
      const result = validateRunTo(nodes, 'b', 'b')
      expect(result.valid).toBe(false)
      expect(result.message).toContain('起始节点不能等于目标节点')
    })
  })
})
