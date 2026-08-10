function looksLikeSourceUuid(value: string): boolean {
  return (
    /^uuid[-_]/i.test(value) ||
    /^[0-9a-f]{32}$/i.test(value) ||
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value
    )
  )
}

export function parseResourceIds(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function parseResourceInputs(
  value: string
): { external_id: string; source_uuid: string }[] {
  const items: { external_id: string; source_uuid: string }[] = []
  value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .forEach((line) => {
      const pair = line
        .split(/[,，]/)
        .map((part) => part.trim())
        .filter(Boolean)
      if (pair.length === 2 && looksLikeSourceUuid(pair[1])) {
        items.push({ external_id: pair[0], source_uuid: pair[1] })
        return
      }
      line
        .split(/[,，]/)
        .map((part) => part.trim())
        .filter(Boolean)
        .forEach((externalId) =>
          items.push({ external_id: externalId, source_uuid: '' })
        )
    })
  return items
}

export function getInteractionQuestion(
  node: Record<string, unknown>
): Record<string, unknown> {
  return node.question && typeof node.question === 'object'
    ? (node.question as Record<string, unknown>)
    : node
}

/** Parse JSON text, returning null instead of throwing on invalid input. */
export function tryParseJson(content: string): unknown | null {
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}
