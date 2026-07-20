import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AssetThumbnail } from './AssetThumbnail'

const originalObserver = window.IntersectionObserver

afterEach(() => {
  cleanup()
  window.IntersectionObserver = originalObserver
})

describe('AssetThumbnail', () => {
  it('defers thumbnail loading until its card approaches the viewport', () => {
    let reveal: ((entries: IntersectionObserverEntry[], observer: IntersectionObserver) => void) | undefined
    window.IntersectionObserver = class {
      constructor(callback: IntersectionObserverCallback) { reveal = callback }
      disconnect = vi.fn()
      observe = vi.fn()
      root = null
      rootMargin = '0px'
      thresholds = []
      takeRecords = () => []
      unobserve = vi.fn()
    } as unknown as typeof IntersectionObserver

    const { container } = render(<AssetThumbnail assetId="asset-1" />)
    expect(container.querySelector('img')).toBeNull()

    act(() => reveal?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver))
    expect(container.querySelector('img')?.getAttribute('src')).toContain('/api/assets/asset-1/thumbnail')
  })

  it('retries after a new thumbnail revision following an image error', () => {
    const { container, rerender } = render(<AssetThumbnail assetId="asset-1" revision={0} />)
    fireEvent.error(container.querySelector('img')!)
    expect(container.querySelector('img')).toBeNull()

    rerender(<AssetThumbnail assetId="asset-1" revision={1} />)
    expect(container.querySelector('img')?.getAttribute('src')).toContain('revision=1')
  })
})
