import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AssetThumbnail } from './AssetThumbnail'

afterEach(cleanup)

describe('AssetThumbnail', () => {
  it('retries after a new thumbnail revision following an image error', () => {
    const { container, rerender } = render(<AssetThumbnail assetId="asset-1" revision={0} />)
    fireEvent.error(container.querySelector('img')!)
    expect(container.querySelector('img')).toBeNull()

    rerender(<AssetThumbnail assetId="asset-1" revision={1} />)
    expect(container.querySelector('img')?.getAttribute('src')).toContain('revision=1')
  })
})
