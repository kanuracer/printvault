import { unzipSync, zipSync } from 'fflate'
import { describe, expect, it } from 'vitest'
import { qualifyThreeMfProductionPaths } from './threeMfProductionPaths'

const rootModel = `<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
  <resources><object id="1"><components><component objectid="1" p:path="/3D/Objects/child.model" /></components></object></resources>
  <build><item objectid="1" /></build>
</model>`

const childModel = `<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources><object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="0" /></vertices><triangles /></mesh></object></resources>
</model>`

function textFromArchive(archive: Record<string, Uint8Array>, path: string): string {
  return new TextDecoder().decode(archive[path])
}

function elementChildren(element: Element, name: string): Element[] {
  return Array.from(element.children).filter((child) => child.localName === name)
}

describe('qualifyThreeMfProductionPaths', () => {
  it('uses p:path to distinguish same local object IDs in external model parts', () => {
    const source = zipSync({
      '3D/3dmodel.model': new TextEncoder().encode(rootModel),
      '3D/Objects/child.model': new TextEncoder().encode(childModel),
    })
    expect(textFromArchive(unzipSync(source), '3D/3dmodel.model')).toContain('<resources>')

    const rewritten = unzipSync(new Uint8Array(qualifyThreeMfProductionPaths(source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength))))
    const root = new DOMParser().parseFromString(textFromArchive(rewritten, '3D/3dmodel.model'), 'application/xml')
    const child = new DOMParser().parseFromString(textFromArchive(rewritten, '3D/Objects/child.model'), 'application/xml')
    expect(root.documentElement.outerHTML).toContain('resources')
    const rootResources = elementChildren(root.documentElement, 'resources')[0]
    const childResources = elementChildren(child.documentElement, 'resources')[0]
    const rootId = elementChildren(rootResources, 'object')[0]?.getAttribute('id')
    const childId = elementChildren(childResources, 'object')[0]?.getAttribute('id')

    expect(rootId).toBeTruthy()
    expect(childId).toBeTruthy()
    expect(rootId).not.toBe(childId)
    expect(elementChildren(elementChildren(root.documentElement, 'build')[0], 'item')[0]?.getAttribute('objectid')).toBe(rootId)
    const component = elementChildren(elementChildren(elementChildren(rootResources, 'object')[0], 'components')[0], 'component')[0]
    expect(component.getAttribute('objectid')).toBe(childId)
    expect(Array.from(component.attributes).some((attribute) => attribute.localName === 'path')).toBe(false)
  })
})
