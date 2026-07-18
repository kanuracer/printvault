export type UserRole = 'viewer' | 'editor' | 'admin'

export type CurrentUser = {
  subject: string
  role: UserRole
}

export type Library = {
  key: string
  name: string
}

export type Project = {
  id: string
  name: string
  description: string
  assetIds: string[]
}

export type Tag = {
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
  metadata: JsonObject
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
    metadata: isObject(value.metadata) ? value.metadata : {},
  }
}

function projectFromJson(value: unknown): Project | null {
  if (!isObject(value)) return null
  const id = stringValue(value.id)
  const name = stringValue(value.name)
  const description = typeof value.description === 'string' ? value.description : ''
  if (!id || !name || !Array.isArray(value.asset_ids)) return null
  return { id, name, description, assetIds: value.asset_ids.filter((item): item is string => typeof item === 'string') }
}

function tagFromJson(value: unknown): Tag | null {
  if (!isObject(value)) return null
  const key = stringValue(value.key)
  const name = stringValue(value.name)
  return key && name ? { key, name } : null
}

async function request(path: string): Promise<unknown> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new ApiError(response.status)
  return response.json()
}

async function requestMutation(path: string, method: 'POST' | 'PUT' | 'DELETE', body?: unknown): Promise<unknown> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) throw new ApiError(response.status)
  return response.status === 204 ? undefined : response.json()
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

export async function getProjects(): Promise<Project[]> {
  const payload = await request('/api/projects')
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => {
    const project = projectFromJson(item)
    return project ? [project] : []
  })
}

export async function createProject(name: string, description: string): Promise<Project> {
  const project = projectFromJson(await requestMutation('/api/projects', 'POST', { name, description }))
  if (!project) throw new ApiError(500)
  return project
}

export async function assignProjectAsset(projectId: string, assetId: string): Promise<Project> {
  const project = projectFromJson(await requestMutation(`/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`, 'PUT'))
  if (!project) throw new ApiError(500)
  return project
}

export async function getTags(): Promise<Tag[]> {
  const payload = await request('/api/tags')
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => {
    const tag = tagFromJson(item)
    return tag ? [tag] : []
  })
}

export async function createTag(key: string, name: string): Promise<Tag> {
  const tag = tagFromJson(await requestMutation('/api/tags', 'POST', { key, name }))
  if (!tag) throw new ApiError(500)
  return tag
}

export async function setAssetTags(id: string, tagKeys: string[]): Promise<Asset> {
  const asset = assetFromJson(await requestMutation(`/api/assets/${encodeURIComponent(id)}/tags`, 'PUT', { tag_keys: tagKeys }))
  if (!asset) throw new ApiError(500)
  return asset
}

export async function getAssets(libraryKey?: string | null): Promise<Asset[]> {
  const payload = await request(libraryKey ? `/api/assets?library=${encodeURIComponent(libraryKey)}` : '/api/assets')
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

export type UploadResponse = {
  items: Asset[]
  rejected: Array<{ filename: string, reason: string }>
}

export async function uploadFiles(libraryKey: string, files: File[]): Promise<UploadResponse> {
  const body = new FormData()
  body.append('library_key', libraryKey)
  files.forEach((file) => body.append('files', file, file.name))
  const response = await fetch('/api/uploads', { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json' }, body })
  if (!response.ok) throw new ApiError(response.status)
  const payload = await response.json()
  if (!isObject(payload) || !Array.isArray(payload.items) || !Array.isArray(payload.rejected)) throw new ApiError(500)
  return {
    items: payload.items.flatMap((item) => {
      const asset = assetFromJson(item)
      return asset ? [asset] : []
    }),
    rejected: payload.rejected.flatMap((item) => isObject(item) && typeof item.filename === 'string' && typeof item.reason === 'string' ? [{ filename: item.filename, reason: item.reason }] : []),
  }
}

export async function archiveAsset(id: string): Promise<Asset> {
  const payload = await requestMutation(`/api/assets/${encodeURIComponent(id)}/archive`, 'POST')
  const asset = assetFromJson(payload)
  if (!asset) throw new ApiError(500)
  return asset
}

export async function restoreAsset(id: string): Promise<Asset> {
  const payload = await requestMutation(`/api/assets/${encodeURIComponent(id)}/restore`, 'POST')
  const asset = assetFromJson(payload)
  if (!asset) throw new ApiError(500)
  return asset
}

export async function deleteAsset(id: string): Promise<void> {
  await requestMutation(`/api/assets/${encodeURIComponent(id)}`, 'DELETE')
}

export function assetThumbnailUrl(id: string): string {
  return `/api/assets/${encodeURIComponent(id)}/thumbnail`
}

export function assetDownloadUrl(id: string): string {
  return `/api/assets/${encodeURIComponent(id)}/download`
}
