export type UserRole = 'viewer' | 'editor' | 'admin'

export type CurrentUser = {
  subject: string
  role: UserRole
}

export type Library = {
  key: string
  name: string
}

export type Asset = {
  id: string
  libraryKey: string
  relativePath: string
  filename: string
  format: string
  favorite: boolean
  tags: string[]
  archived: boolean
  byteSize?: number
}

type JsonObject = Record<string, unknown>

export class ApiError extends Error {
  constructor(readonly status: number) {
    super(`API request failed with status ${status}`)
  }
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function assetFromJson(value: unknown): Asset | null {
  if (!isObject(value)) return null
  const id = stringValue(value.id)
  const libraryKey = stringValue(value.library_key)
  const relativePath = stringValue(value.relative_path)
  const filename = stringValue(value.filename)
  const format = stringValue(value.format)
  if (!id || !libraryKey || !relativePath || !filename || !format) return null

  return {
    id,
    libraryKey,
    relativePath,
    filename,
    format,
    favorite: value.favorite === true,
    tags: Array.isArray(value.tags) ? value.tags.filter((tag): tag is string => typeof tag === 'string') : [],
    archived: value.archived === true,
    byteSize: typeof value.byte_size === 'number' && Number.isFinite(value.byte_size) ? value.byte_size : undefined,
  }
}

async function request(path: string): Promise<unknown> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new ApiError(response.status)
  return response.json()
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const payload = await request('/api/auth/me')
  if (!isObject(payload)) throw new ApiError(500)
  const subject = stringValue(payload.subject)
  const role = stringValue(payload.role)
  if (!subject || !role || !['viewer', 'editor', 'admin'].includes(role)) throw new ApiError(500)
  return { subject, role: role as UserRole }
}

export async function getLibraries(): Promise<Library[]> {
  const payload = await request('/api/libraries')
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => {
    if (!isObject(item)) return []
    const key = stringValue(item.key)
    const name = stringValue(item.name)
    return key && name ? [{ key, name }] : []
  })
}

export async function getAssets(): Promise<Asset[]> {
  const payload = await request('/api/assets')
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => {
    const asset = assetFromJson(item)
    return asset ? [asset] : []
  })
}

export async function getAsset(id: string): Promise<Asset> {
  const payload = await request(`/api/assets/${encodeURIComponent(id)}`)
  const asset = assetFromJson(payload)
  if (!asset) throw new ApiError(500)
  return asset
}

export function assetDownloadUrl(id: string): string {
  return `/api/assets/${encodeURIComponent(id)}/download`
}
