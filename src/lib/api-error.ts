const MAX_ERROR_LENGTH = 800

function bounded(value: string): string {
  const normalized = value.replace(/\s+/g, ' ').trim()
  return normalized.length > MAX_ERROR_LENGTH
    ? `${normalized.slice(0, MAX_ERROR_LENGTH - 1)}…`
    : normalized
}

export function formatApiErrorDetail(value: unknown, fallback = 'Request failed'): string {
  if (typeof value === 'string' && value.trim()) return bounded(value)
  if (Array.isArray(value)) {
    const messages = value.map((item) => {
      if (!item || typeof item !== 'object') return String(item)
      const record = item as Record<string, unknown>
      const location = Array.isArray(record.loc) ? record.loc.map(String).join('.') : ''
      const message = typeof record.msg === 'string'
        ? record.msg
        : typeof record.message === 'string'
          ? record.message
          : JSON.stringify(record)
      return location ? `${location}: ${message}` : message
    }).filter(Boolean)
    return messages.length ? bounded(messages.join('; ')) : fallback
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['detail', 'message', 'reason', 'error']) {
      if (record[key] !== undefined && record[key] !== value) {
        return formatApiErrorDetail(record[key], fallback)
      }
    }
    try {
      return bounded(JSON.stringify(record)) || fallback
    } catch {
      return fallback
    }
  }
  return fallback
}
