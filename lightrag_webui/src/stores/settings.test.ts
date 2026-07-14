import { describe, expect, test } from 'bun:test'

import { coerceQueryLabel } from '@/stores/settings'
import { defaultQueryLabel } from '@/lib/constants'

describe('coerceQueryLabel', () => {
  test('coerces an empty persisted label back to the default', () => {
    // The graph clears queryLabel to '' when a fetch returns an empty graph
    // (e.g. documents still indexing). That transient sentinel must never
    // survive a reload, or the graph wedges into "Empty (Try Reload Again)"
    // and never issues another /graphs request.
    expect(coerceQueryLabel('')).toBe(defaultQueryLabel)
  })

  test('coerces a whitespace-only label back to the default', () => {
    expect(coerceQueryLabel('   ')).toBe(defaultQueryLabel)
  })

  test('coerces null/undefined back to the default', () => {
    expect(coerceQueryLabel(null)).toBe(defaultQueryLabel)
    expect(coerceQueryLabel(undefined)).toBe(defaultQueryLabel)
  })

  test('passes a real user-selected label through unchanged', () => {
    expect(coerceQueryLabel('Scrooge')).toBe('Scrooge')
  })

  test('passes the wildcard label through unchanged', () => {
    expect(coerceQueryLabel('*')).toBe('*')
  })
})
