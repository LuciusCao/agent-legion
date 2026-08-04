/**
 * Convert valid LaTeX emitted by some question sources into syntax supported by
 * KaTeX. MathType commonly exports a one-column array with the LaTeX `array`
 * package shorthand `*{20}{l}`. KaTeX does not implement that shorthand and
 * would otherwise fall back to displaying the source text.
 */
export function normalizeLatexForKatex(latex: string): string {
  return latex.replace(
    /\\begin\{array\}\{\*\{\d+\}\{([lcr])\}\}/g,
    '\\begin{array}{$1}'
  )
}
