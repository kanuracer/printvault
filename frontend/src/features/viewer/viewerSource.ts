export const MAX_MODEL_BYTES = 100 * 1024 * 1024

const modelFormats = ['stl', 'obj', '3mf'] as const

export type ModelFormat = (typeof modelFormats)[number]
export type ModelLoaderName = ModelFormat

export type ViewerSource = {
  kind: 'object-url' | 'authenticated-api'
  url: string
  byteSize?: number
  format?: ModelFormat
}

export type ValidViewerSource = ViewerSource & { format: ModelFormat }

export type ViewerSourceValidation =
  | { ok: true; source: ValidViewerSource }
  | { ok: false; reason: 'unsupported-source' | 'unsupported-format' | 'oversized' }

export function isModelFormat(value: unknown): value is ModelFormat {
  return typeof value === 'string' && (modelFormats as readonly string[]).includes(value.toLowerCase())
}

export function selectModelLoader(format: ModelFormat): ModelLoaderName {
  return format
}

function inferFormat(url: string): ModelFormat | undefined {
  const extension = url.split(/[?#]/, 1)[0].split('.').pop()?.toLowerCase()
  return isModelFormat(extension) ? extension : undefined
}

function isSafeApiUrl(url: string, origin: string): boolean {
  try {
    const parsed = new URL(url, origin)
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:')
      && parsed.origin === origin
      && parsed.pathname.startsWith('/api/')
  } catch {
    return false
  }
}

function isObjectUrl(url: string): boolean {
  try {
    return new URL(url).protocol === 'blob:'
  } catch {
    return false
  }
}

export function validateViewerSource(
  source: ViewerSource | null | undefined,
  origin = globalThis.location?.origin ?? 'http://localhost',
): ViewerSourceValidation {
  if (!source || typeof source.url !== 'string') {
    return { ok: false, reason: 'unsupported-source' }
  }

  const byteSize = source.byteSize
  if (byteSize !== undefined && (!Number.isFinite(byteSize) || byteSize < 0 || byteSize > MAX_MODEL_BYTES)) {
    return { ok: false, reason: 'oversized' }
  }

  const isAllowedSource = source.kind === 'object-url'
    ? isObjectUrl(source.url)
    : source.kind === 'authenticated-api' && isSafeApiUrl(source.url, origin)

  if (!isAllowedSource) {
    return { ok: false, reason: 'unsupported-source' }
  }

  const format = source.format ?? inferFormat(source.url)
  if (!isModelFormat(format)) {
    return { ok: false, reason: 'unsupported-format' }
  }

  return { ok: true, source: { ...source, format } }
}
