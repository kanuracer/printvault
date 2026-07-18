import de from './de.json'
import en from './en.json'
import { describe, expect, it } from 'vitest'

const viewerKeys = ['loading', 'unsupported', 'oversized', 'parseFailure', 'resetView', 'wireframe', 'grid', 'axes'] as const

describe('viewer locale resources', () => {
  it.each([['de', de], ['en', en]] as const)('defines every visible viewer label in %s', (_locale, messages) => {
    expect(messages).toHaveProperty('viewer')

    for (const key of viewerKeys) {
      expect(messages.viewer).toHaveProperty(key)
      expect(messages.viewer[key]).not.toEqual('')
    }
  })
})
