import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BoxGeometry, Group, Mesh, MeshPhongMaterial } from 'three'
import '../../i18n'
import { applyPreviewMaterial, ModelViewer } from './ModelViewer'

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

describe('applyPreviewMaterial', () => {
  it('keeps source material colors instead of replacing them with the fallback preview color', () => {
    const geometry = new BoxGeometry(1, 1, 1)
    const sourceMaterial = new MeshPhongMaterial({ color: 0xf97316 })
    const root = new Mesh(geometry, sourceMaterial)

    const materials = applyPreviewMaterial(root, false)

    expect(root.material).toBe(sourceMaterial)
    expect(sourceMaterial.color.getHex()).toBe(0xf97316)
    expect(materials).toContain(sourceMaterial)

    geometry.dispose()
    sourceMaterial.dispose()
  })

  it('applies Bambu project colors by 3MF build-item order without changing shared source materials', () => {
    const geometry = new BoxGeometry(1, 1, 1)
    const sharedMaterial = new MeshPhongMaterial({ color: 0xffffff })
    const root = new Group()
    const firstBuildItem = new Group()
    const secondBuildItem = new Group()
    const firstMesh = new Mesh(geometry, sharedMaterial)
    const secondMesh = new Mesh(geometry.clone(), sharedMaterial)
    firstBuildItem.add(firstMesh)
    secondBuildItem.add(secondMesh)
    root.add(firstBuildItem, secondBuildItem)

    const materials = applyPreviewMaterial(root, false, ['#ef4444', '#22c55e'])

    expect(firstMesh.material).not.toBe(sharedMaterial)
    expect(secondMesh.material).not.toBe(sharedMaterial)
    expect((firstMesh.material as MeshPhongMaterial).color.getStyle()).toBe('rgb(239,68,68)')
    expect((secondMesh.material as MeshPhongMaterial).color.getStyle()).toBe('rgb(34,197,94)')
    expect(sharedMaterial.color.getHex()).toBe(0xffffff)
    expect(materials).toContain(firstMesh.material)
    expect(materials).toContain(secondMesh.material)

    geometry.dispose()
    sharedMaterial.dispose()
    ;(firstMesh.material as MeshPhongMaterial).dispose()
    ;(secondMesh.material as MeshPhongMaterial).dispose()
    secondMesh.geometry.dispose()
  })

})
