import { unzipSync, zipSync } from 'fflate'

const MODEL_PART = /^3D\/.+\.model$/i
const textDecoder = new TextDecoder()
const textEncoder = new TextEncoder()

function directChildren(element: Element, name: string): Element[] {
  return Array.from(element.children).filter((child) => child.localName === name)
}

function productionPath(component: Element): string | null {
  return Array.from(component.attributes).find((attribute) => attribute.localName === 'path')?.value ?? null
}

function normalizePartPath(value: string): string | null {
  const normalized = value.replaceAll('\\', '/').replace(/^\/+/, '')
  if (!normalized || normalized.split('/').some((segment) => segment === '.' || segment === '..' || !segment)) return null
  return normalized
}

function archiveBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

/**
 * Three.js does not resolve Bambu/Orca's production-extension `p:path`.
 * Their external model parts reuse local object IDs, so qualify every ID
 * before handing the archive to ThreeMFLoader.
 */
export function qualifyThreeMfProductionPaths(source: ArrayBuffer): ArrayBuffer {
  const archive = unzipSync(new Uint8Array(source))
  const partNames = Object.keys(archive).filter((name) => MODEL_PART.test(name))
  if (partNames.length === 0) return source

  const parser = new DOMParser()
  const documents = new Map<string, Document>()
  for (const name of partNames) {
    const document = parser.parseFromString(textDecoder.decode(archive[name]), 'application/xml')
    if (document.getElementsByTagName('parsererror').length > 0 || document.documentElement.localName !== 'model') return source
    documents.set(name, document)
  }

  const identifiers = new Map<string, string>()
  let nextIdentifier = 1
  for (const name of partNames) {
    const resources = directChildren(documents.get(name)!.documentElement, 'resources')[0]
    if (!resources) continue
    for (const object of directChildren(resources, 'object')) {
      const objectId = object.getAttribute('id')
      if (!objectId) return source
      identifiers.set(`${name}\u0000${objectId}`, `pv${nextIdentifier++}`)
    }
  }

  let hasProductionPaths = false
  for (const name of partNames) {
    const document = documents.get(name)!
    const resources = directChildren(document.documentElement, 'resources')[0]
    if (resources) {
      for (const object of directChildren(resources, 'object')) {
        const objectId = object.getAttribute('id')
        const replacement = objectId ? identifiers.get(`${name}\u0000${objectId}`) : undefined
        if (!replacement) return source
        object.setAttribute('id', replacement)

        const components = directChildren(object, 'components')[0]
        if (!components) continue
        for (const component of directChildren(components, 'component')) {
          const objectReference = component.getAttribute('objectid')
          const path = productionPath(component)
          const target = path === null ? name : normalizePartPath(path)
          if (path !== null) hasProductionPaths = true
          const replacementReference = objectReference && target ? identifiers.get(`${target}\u0000${objectReference}`) : undefined
          if (!replacementReference) return source
          component.setAttribute('objectid', replacementReference)
          if (path !== null) Array.from(component.attributes).filter((attribute) => attribute.localName === 'path').forEach((attribute) => component.removeAttributeNode(attribute))
        }
      }
    }

    const build = directChildren(document.documentElement, 'build')[0]
    if (!build) continue
    for (const item of directChildren(build, 'item')) {
      const objectReference = item.getAttribute('objectid')
      const replacement = objectReference ? identifiers.get(`${name}\u0000${objectReference}`) : undefined
      if (!replacement) return source
      item.setAttribute('objectid', replacement)
    }
  }

  if (!hasProductionPaths) return source
  const serializer = new XMLSerializer()
  for (const [name, document] of documents) archive[name] = textEncoder.encode(serializer.serializeToString(document))
  return archiveBuffer(zipSync(archive, { level: 6 }))
}
