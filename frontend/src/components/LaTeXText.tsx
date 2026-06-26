import { RichText } from './RichText'

interface LaTeXTextProps {
  children: string | string[]
}

export function LaTeXText({ children }: LaTeXTextProps) {
  const text =
    typeof children === 'string'
      ? children
      : Array.isArray(children)
        ? children.join('')
        : ''
  return (
    <span data-testid="latex-text">
      <RichText mode="inline">{text}</RichText>
    </span>
  )
}
