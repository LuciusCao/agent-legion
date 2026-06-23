import { useState, useCallback, useMemo } from 'react'
import { MaterialIcon } from './MaterialIcon'
import styles from './JsonTree.module.css'

export interface JsonTreeProps {
  data: unknown
}

function isCollapsible(
  value: unknown
): value is Record<string, unknown> | unknown[] {
  return typeof value === 'object' && value !== null
}

function countItems(value: Record<string, unknown> | unknown[]): number {
  return Array.isArray(value) ? value.length : Object.keys(value).length
}

function getPrimitiveClass(value: unknown): string {
  if (value === null) return styles.nullValue
  if (typeof value === 'string') return styles.stringValue
  if (typeof value === 'number') return styles.numberValue
  if (typeof value === 'boolean') return styles.booleanValue
  return ''
}

function formatPrimitive(value: unknown): string {
  if (typeof value === 'string') {
    return JSON.stringify(value)
  }
  return String(value)
}

type JsonLine =
  | {
      type: 'object'
      path: string
      depth: number
      key?: string
      value: Record<string, unknown>
      isCollapsed: boolean
    }
  | {
      type: 'array'
      path: string
      depth: number
      key?: string
      value: unknown[]
      isCollapsed: boolean
    }
  | {
      type: 'primitive'
      path: string
      depth: number
      key?: string
      value: unknown
    }
  | {
      type: 'close'
      path: string
      depth: number
      bracket: '}' | ']'
    }

function flattenJson(
  value: unknown,
  path: string,
  depth: number,
  key: string | undefined,
  collapsed: Set<string>
): JsonLine[] {
  if (!isCollapsible(value)) {
    return [{ type: 'primitive', path, depth, key, value }]
  }

  const isArray = Array.isArray(value)
  const lines: JsonLine[] = []

  if (isArray) {
    lines.push({
      type: 'array',
      path,
      depth,
      key,
      value,
      isCollapsed: collapsed.has(path),
    })
  } else {
    lines.push({
      type: 'object',
      path,
      depth,
      key,
      value,
      isCollapsed: collapsed.has(path),
    })
  }

  if (!collapsed.has(path)) {
    if (isArray) {
      value.forEach((item, index) => {
        lines.push(
          ...flattenJson(
            item,
            `${path}[${index}]`,
            depth + 1,
            undefined,
            collapsed
          )
        )
      })
    } else {
      Object.entries(value).forEach(([entryKey, entryValue]) => {
        lines.push(
          ...flattenJson(
            entryValue,
            `${path}.${entryKey}`,
            depth + 1,
            entryKey,
            collapsed
          )
        )
      })
    }
    lines.push({ type: 'close', path, depth, bracket: isArray ? ']' : '}' })
  }

  return lines
}

function renderLineContent(line: JsonLine): React.ReactNode {
  switch (line.type) {
    case 'object':
      return (
        <>
          {line.key !== undefined && (
            <>
              <span className={styles.key}>{line.key}</span>
              <span className={styles.separator}>: </span>
            </>
          )}
          {line.isCollapsed ? (
            <span className={styles.collapsed}>
              {'{...}'}
              <span className={styles.count}>
                {' '}
                // {countItems(line.value)} items
              </span>
            </span>
          ) : (
            <span className={styles.bracket}>{'{'}</span>
          )}
        </>
      )
    case 'array':
      return (
        <>
          {line.key !== undefined && (
            <>
              <span className={styles.key}>{line.key}</span>
              <span className={styles.separator}>: </span>
            </>
          )}
          {line.isCollapsed ? (
            <span className={styles.collapsed}>
              {'[...]'}
              <span className={styles.count}>
                {' '}
                // {countItems(line.value)} items
              </span>
            </span>
          ) : (
            <span className={styles.bracket}>{'['}</span>
          )}
        </>
      )
    case 'close':
      return <span className={styles.bracket}>{line.bracket}</span>
    default:
      return (
        <>
          {line.key !== undefined && (
            <>
              <span className={styles.key}>{line.key}</span>
              <span className={styles.separator}>: </span>
            </>
          )}
          <span
            className={`${styles.primitive} ${getPrimitiveClass(line.value)}`}
          >
            {formatPrimitive(line.value)}
          </span>
        </>
      )
  }
}

export function JsonTree({ data }: JsonTreeProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const handleToggle = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  const expandAll = useCallback(() => {
    setCollapsed(new Set())
  }, [])

  const collapseAll = useCallback(() => {
    const next = new Set<string>()
    const collect = (value: unknown, currentPath: string) => {
      if (!isCollapsible(value)) return
      next.add(currentPath)
      if (Array.isArray(value)) {
        value.forEach((item, index) =>
          collect(item, `${currentPath}[${index}]`)
        )
      } else {
        Object.entries(value).forEach(([key, val]) =>
          collect(val, `${currentPath}.${key}`)
        )
      }
    }
    collect(data, 'root')
    setCollapsed(next)
  }, [data])

  const lines = useMemo(
    () => flattenJson(data, 'root', 0, undefined, collapsed),
    [data, collapsed]
  )

  return (
    <div className={styles.tree}>
      <div className={styles.toolbar}>
        <button
          type="button"
          className={styles.toolbarButton}
          onClick={expandAll}
        >
          全部展开
        </button>
        <button
          type="button"
          className={styles.toolbarButton}
          onClick={collapseAll}
        >
          全部折叠
        </button>
      </div>
      <div className={styles.editor}>
        {lines.map((line, index) => {
          const hasToggle = line.type === 'object' || line.type === 'array'
          const isCollapsed =
            hasToggle &&
            (line as JsonLine & { isCollapsed?: boolean }).isCollapsed
          return (
            <div key={`${line.type}-${line.path}`} className={styles.row}>
              <div className={styles.gutter}>
                <span className={styles.gutterToggle}>
                  {hasToggle ? (
                    <button
                      type="button"
                      className={styles.toggle}
                      onClick={() => handleToggle(line.path)}
                      aria-label={isCollapsed ? '展开' : '折叠'}
                    >
                      <MaterialIcon
                        name={isCollapsed ? 'chevron_right' : 'expand_more'}
                      />
                    </button>
                  ) : null}
                </span>
                <span className={styles.lineNumber}>{index + 1}</span>
              </div>
              <div
                className={styles.content}
                style={{ paddingLeft: `${line.depth * 2}ch` }}
              >
                {renderLineContent(line)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
