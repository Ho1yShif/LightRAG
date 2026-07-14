import { beforeAll, describe, expect, test } from 'bun:test'

type SettingsModule = typeof import('@/stores/settings')
type ConstantsModule = typeof import('@/lib/constants')

// Seed a synchronous localStorage holding the poisoned `queryLabel: ''` BEFORE
// importing the store, so zustand's persist middleware rehydrates from it during
// module init. This exercises the real bug path: the graph view clears queryLabel
// to '' when a fetch returns an empty graph (e.g. while documents are still
// indexing); that transient sentinel must be coerced back to the default on load,
// or the graph wedges on "Empty (Try Reload Again)" and never re-fetches.
const storageMock = (initial: Record<string, string> = {}) => {
  const data = new Map<string, string>(Object.entries(initial))

  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value)
    },
    removeItem: (key: string) => {
      data.delete(key)
    },
    clear: () => {
      data.clear()
    }
  }
}

let settings: SettingsModule
let constants: ConstantsModule

beforeAll(async () => {
  Object.defineProperty(globalThis, 'localStorage', {
    value: storageMock({
      'settings-storage': JSON.stringify({ state: { queryLabel: '' }, version: 20 })
    }),
    configurable: true
  })

  constants = await import('@/lib/constants')
  settings = await import('@/stores/settings')
})

describe('settings persist rehydration', () => {
  test('recovers an empty persisted queryLabel to the default on load', () => {
    expect(settings.useSettingsStore.getState().queryLabel).toBe(constants.defaultQueryLabel)
  })
})
