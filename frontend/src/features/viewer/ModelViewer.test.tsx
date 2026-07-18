import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import '../../i18n'
import { ModelViewer } from './ModelViewer'

const source = {
  kind: 'authenticated-api' as const,
  url: '/api/models/nozzle.stl',
  byteSize: 1_024,
}

afterEach(cleanup)

describe('ModelViewer validation states', () => {
  it('renders the localized unsupported state without creating a canvas for a rejected endpoint', () => {
    render(<ModelViewer source={{ ...source, url: 'https://untrusted.example/nozzle.stl' }} />)

    expect(screen.getByText('Nicht unterstütztes Modell')).toBeVisible()
    expect(document.querySelector('canvas')).toBeNull()
  })

  it('renders the localized oversized state before a loader can parse the source', () => {
    render(<ModelViewer source={{ ...source, byteSize: 100 * 1024 * 1024 + 1 }} />)

    expect(screen.getByText('Die Modelldatei ist zu groß')).toBeVisible()
    expect(document.querySelector('canvas')).toBeNull()
  })

  it('revokes an object URL when an invalid source is unmounted', () => {
    const objectUrl = 'blob:https://printvault.example/1f9e9fdb-8784-4e16-8f62-8e8d99d63b8f'
    const descriptor = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const { unmount } = render(<ModelViewer source={{
      kind: 'object-url',
      url: objectUrl,
      byteSize: 100 * 1024 * 1024 + 1,
      format: 'stl',
    }} />)

    unmount()

    expect(revokeObjectURL).toHaveBeenCalledWith(objectUrl)
    if (descriptor) Object.defineProperty(URL, 'revokeObjectURL', descriptor)
    else Reflect.deleteProperty(URL, 'revokeObjectURL')
  })
})
