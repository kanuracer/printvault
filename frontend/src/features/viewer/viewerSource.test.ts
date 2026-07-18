import { describe, expect, it } from 'vitest'
import {
  MAX_MODEL_BYTES,
  isModelFormat,
  selectModelLoader,
  validateViewerSource,
  type ViewerSource,
} from './viewerSource'

const apiSource = (overrides: Partial<ViewerSource> = {}): ViewerSource => ({
  kind: 'authenticated-api',
  url: '/api/models/nozzle.stl',
  byteSize: 1_024,
  ...overrides,
})

describe('model viewer source validation', () => {
  it('accepts a bounded same-origin authenticated API source and dispatches its STL loader', () => {
    const result = validateViewerSource(apiSource(), 'https://printvault.example')

    expect(result).toEqual({
      ok: true,
      source: expect.objectContaining({ format: 'stl', url: '/api/models/nozzle.stl' }),
    })
    expect(selectModelLoader('stl')).toBe('stl')
  })

  it.each([
    ['obj', 'obj'],
    ['3mf', '3mf'],
  ] as const)('dispatches the %s format to its loader', (extension, format) => {
    const result = validateViewerSource(
      apiSource({ url: `/api/models/model.${extension}` }),
      'https://printvault.example',
    )

    expect(result).toEqual({ ok: true, source: expect.objectContaining({ format }) })
    expect(selectModelLoader(format)).toBe(format)
  })

  it('rejects URLs that are not same-origin authenticated API endpoints', () => {
    expect(validateViewerSource(apiSource({ url: 'https://untrusted.example/model.stl' }), 'https://printvault.example'))
      .toEqual({ ok: false, reason: 'unsupported-source' })
    expect(validateViewerSource(apiSource({ url: '/downloads/model.stl' }), 'https://printvault.example'))
      .toEqual({ ok: false, reason: 'unsupported-source' })
  })

  it('accepts only blob URLs when a parent supplies an object URL', () => {
    expect(validateViewerSource({
      kind: 'object-url',
      url: 'blob:https://printvault.example/1f9e9fdb-8784-4e16-8f62-8e8d99d63b8f',
      byteSize: 400,
      format: 'obj',
    }, 'https://printvault.example')).toEqual({
      ok: true,
      source: expect.objectContaining({ format: 'obj' }),
    })

    expect(validateViewerSource(apiSource({ kind: 'object-url', url: '/api/models/model.obj' }), 'https://printvault.example'))
      .toEqual({ ok: false, reason: 'unsupported-source' })
  })

  it('rejects an unsupported filename extension before dispatching a loader', () => {
    const result = validateViewerSource(apiSource({ url: '/api/models/model.gltf' }), 'https://printvault.example')

    expect(result).toEqual({ ok: false, reason: 'unsupported-format' })
    expect(isModelFormat('gltf')).toBe(false)
    expect(isModelFormat('3mf')).toBe(true)
  })

  it('rejects absent, negative, and oversized byte sizes before parsing', () => {
    expect(validateViewerSource(apiSource({ byteSize: Number.NaN }), 'https://printvault.example'))
      .toEqual({ ok: false, reason: 'oversized' })
    expect(validateViewerSource(apiSource({ byteSize: -1 }), 'https://printvault.example'))
      .toEqual({ ok: false, reason: 'oversized' })
    expect(validateViewerSource(apiSource({ byteSize: MAX_MODEL_BYTES + 1 }), 'https://printvault.example'))
      .toEqual({ ok: false, reason: 'oversized' })
  })
})
