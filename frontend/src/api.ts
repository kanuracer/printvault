export type UserRole = 'viewer' | 'editor' | 'admin'
export type AppearancePreference = 'dark' | 'light' | 'system'
export type ExplorerView = 'grid' | 'list'
export type ExplorerPreference = { view: ExplorerView, pageSize: 25 | 50 | 100 }

export type CurrentUser = {
  subject: string
  role: UserRole
}

export type Library = {
  key: string
  name: string
}

export type LibraryExcludeRule = {
  pattern: string
}

export type Project = {
  id: string
  name: string
  description: string
  assetIds: string[]
  folders: ProjectFolder[]
  assetFolderIds: Record<string, string>
}

export type ProjectFolder = {
  id: string
  name: string
  parentId: string | null
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

export type AssetPage = {
  items: Asset[]
  total: number
  limit: number
  offset: number
}

export type HelperPairingCode = {
  pairingCode: string
  expiresAt: string
}

export type HelperDevice = {
  deviceId: string
  name: string
  createdAt: string | null
}

export type AssetPageQuery = {
  libraryKey?: string | null
  projectId?: string | null
  folderId?: string | null
  limit?: number
  offset?: number
}

type JsonObject = Record<string, unknown>

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string | null = null) {
    super(detail ?? `API request failed with status ${status}`)
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
  const folders = Array.isArray(value.folders) ? value.folders.flatMap((folder) => {
    if (!isObject(folder)) return []
    const folderId = stringValue(folder.id)
    const folderName = stringValue(folder.name)
    return folderId && folderName && (folder.parent_id === null || typeof folder.parent_id === 'string') ? [{ id: folderId, name: folderName, parentId: folder.parent_id }] : []
  }) : []
  const assetFolderIds = isObject(value.asset_folder_ids) ? Object.fromEntries(Object.entries(value.asset_folder_ids).filter((entry): entry is [string, string] => typeof entry[1] === 'string')) : {}
  return { id, name, description, assetIds: value.asset_ids.filter((item): item is string => typeof item === 'string'), folders, assetFolderIds }
}

function tagFromJson(value: unknown): Tag | null {
  if (!isObject(value)) return null
  const key = stringValue(value.key)
  const name = stringValue(value.name)
  return key && name ? { key, name } : null
}

function helperPairingCodeFromJson(value: unknown): HelperPairingCode | null {
  if (!isObject(value)) return null
  const pairingCode = stringValue(value.pairing_code)
  const expiresAt = stringValue(value.expires_at)
  return pairingCode && expiresAt ? { pairingCode, expiresAt } : null
}

function helperDeviceFromJson(value: unknown): HelperDevice | null {
  if (!isObject(value)) return null
  const deviceId = stringValue(value.device_id)
  const name = stringValue(value.name)
  if (!deviceId || !name) return null
  return {
    deviceId,
    name,
    createdAt: value.created_at === undefined ? null : stringValue(value.created_at),
  }
}

async function request(path: string): Promise<unknown> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.json()
}

async function requestMutation(path: string, method: 'POST' | 'PUT' | 'DELETE', body?: unknown): Promise<unknown> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (!response.ok) throw await apiErrorFromResponse(response)
  return response.status === 204 ? undefined : response.json()
}

async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  let detail: string | null = null
  try {
    const payload = await response.json()
    detail = isObject(payload) && typeof payload.detail === 'string' && payload.detail ? payload.detail : null
  } catch {
    detail = null
  }
  return new ApiError(response.status, detail)
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const payload = await request('/api/auth/me')
  if (!isObject(payload)) throw new ApiError(500)
  const subject = stringValue(payload.subject)
  const role = stringValue(payload.role)
  if (!subject || !role || !['viewer', 'editor', 'admin'].includes(role)) throw new ApiError(500)
  return { subject, role: role as UserRole }
}

function appearancePreferenceFromJson(payload: unknown): AppearancePreference {
  if (!isObject(payload) || !['dark', 'light', 'system'].includes(payload.appearance as string)) throw new ApiError(500)
  return payload.appearance as AppearancePreference
}

export async function getAppearancePreference(): Promise<AppearancePreference> {
  return appearancePreferenceFromJson(await request('/api/preferences/appearance'))
}

export async function setAppearancePreference(appearance: AppearancePreference): Promise<AppearancePreference> {
  return appearancePreferenceFromJson(await requestMutation('/api/preferences/appearance', 'PUT', { appearance }))
}

function explorerPreferenceFromJson(payload: unknown): ExplorerPreference {
  if (!isObject(payload) || (payload.view !== 'grid' && payload.view !== 'list') || ![25, 50, 100].includes(payload.page_size as number)) throw new ApiError(500)
  return { view: payload.view, pageSize: payload.page_size as ExplorerPreference['pageSize'] }
}

export async function getExplorerPreference(): Promise<ExplorerPreference> {
  return explorerPreferenceFromJson(await request('/api/preferences/explorer'))
}

export async function setExplorerPreference(view: ExplorerView, pageSize: ExplorerPreference['pageSize']): Promise<ExplorerPreference> {
  return explorerPreferenceFromJson(await requestMutation('/api/preferences/explorer', 'PUT', { view, page_size: pageSize }))
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

export async function getLibraryExcludeRules(libraryKey: string): Promise<LibraryExcludeRule[]> {
  const payload = await request(`/api/admin/libraries/${encodeURIComponent(libraryKey)}/exclude-rules`)
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => isObject(item) && typeof item.pattern === 'string' && item.pattern ? [{ pattern: item.pattern }] : [])
}

export async function addLibraryExcludeRule(libraryKey: string, pattern: string): Promise<LibraryExcludeRule[]> {
  const payload = await requestMutation(`/api/admin/libraries/${encodeURIComponent(libraryKey)}/exclude-rules`, 'POST', { pattern })
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => isObject(item) && typeof item.pattern === 'string' && item.pattern ? [{ pattern: item.pattern }] : [])
}

export async function removeLibraryExcludeRule(libraryKey: string, pattern: string): Promise<LibraryExcludeRule[]> {
  const payload = await requestMutation(`/api/admin/libraries/${encodeURIComponent(libraryKey)}/exclude-rules`, 'DELETE', { pattern })
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => isObject(item) && typeof item.pattern === 'string' && item.pattern ? [{ pattern: item.pattern }] : [])
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

export async function assignProjectAsset(projectId: string, assetId: string, folderId: string | null = null): Promise<Project> {
  const project = projectFromJson(await requestMutation(`/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`, 'PUT', folderId === null ? {} : { folder_id: folderId }))
  if (!project) throw new ApiError(500)
  return project
}

export async function assignProjectAssetsBatch(projectId: string, assetIds: string[], folderId: string | null = null): Promise<Project> {
  const project = projectFromJson(await requestMutation(`/api/projects/${encodeURIComponent(projectId)}/assets/batch`, 'PUT', { asset_ids: assetIds, ...(folderId === null ? {} : { folder_id: folderId }) }))
  if (!project) throw new ApiError(500)
  return project
}

export async function removeProjectAsset(projectId: string, assetId: string): Promise<Project> {
  const project = projectFromJson(await requestMutation(`/api/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`, 'DELETE'))
  if (!project) throw new ApiError(500)
  return project
}

export async function createProjectFolder(projectId: string, name: string, parentId: string | null): Promise<ProjectFolder> {
  const payload = await requestMutation(`/api/projects/${encodeURIComponent(projectId)}/folders`, 'POST', { name, parent_id: parentId })
  if (!isObject(payload)) throw new ApiError(500)
  const id = stringValue(payload.id)
  const folderName = stringValue(payload.name)
  if (!id || !folderName || (payload.parent_id !== null && typeof payload.parent_id !== 'string')) throw new ApiError(500)
  return { id, name: folderName, parentId: payload.parent_id }
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

export async function setAssetTagsBatch(assetIds: string[], tagKeys: string[]): Promise<Asset[]> {
  const payload = await requestMutation('/api/assets/batch/tags', 'POST', { asset_ids: assetIds, tag_keys: tagKeys })
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  const assets = payload.items.flatMap((item) => {
    const asset = assetFromJson(item)
    return asset ? [asset] : []
  })
  if (assets.length !== assetIds.length) throw new ApiError(500)
  return assets
}

export async function getAssetPage(query: AssetPageQuery = {}): Promise<AssetPage> {
  const parameters = new URLSearchParams()
  if (query.libraryKey) parameters.set('library', query.libraryKey)
  if (query.projectId) parameters.set('project_id', query.projectId)
  if (query.folderId) parameters.set('folder_id', query.folderId)
  if (query.limit !== undefined && query.limit !== 50) parameters.set('limit', String(query.limit))
  if (query.offset !== undefined && query.offset !== 0) parameters.set('offset', String(query.offset))
  const payload = await request(`/api/assets${parameters.size ? `?${parameters.toString()}` : ''}`)
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  const items = payload.items.flatMap((item) => {
    const asset = assetFromJson(item)
    return asset ? [asset] : []
  })
  const total = typeof payload.total === 'number' && Number.isInteger(payload.total) && payload.total >= 0 ? payload.total : items.length
  const limit = typeof payload.limit === 'number' && Number.isInteger(payload.limit) && payload.limit > 0 ? payload.limit : query.limit ?? 50
  const offset = typeof payload.offset === 'number' && Number.isInteger(payload.offset) && payload.offset >= 0 ? payload.offset : query.offset ?? 0
  return { items, total, limit, offset }
}

export async function getAssets(libraryKey?: string | null): Promise<Asset[]> {
  return (await getAssetPage({ libraryKey })).items
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

export type UploadCollisionPolicy = 'reject' | 'overwrite' | 'rename'

export async function uploadFiles(libraryKey: string, files: File[], collisionPolicy: UploadCollisionPolicy = 'reject'): Promise<UploadResponse> {
  const body = new FormData()
  body.append('library_key', libraryKey)
  body.append('collision_policy', collisionPolicy)
  files.forEach((file) => body.append('files', file, file.name))
  const response = await fetch('/api/uploads', { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json' }, body })
  if (!response.ok) throw await apiErrorFromResponse(response)
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

export async function archiveAssetsBatch(assetIds: string[]): Promise<Asset[]> {
  const payload = await requestMutation('/api/assets/batch/archive', 'POST', { asset_ids: assetIds })
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  const assets = payload.items.flatMap((item) => {
    const asset = assetFromJson(item)
    return asset ? [asset] : []
  })
  if (assets.length !== assetIds.length) throw new ApiError(500)
  return assets
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

export async function uploadAssetThumbnail(id: string, image: File): Promise<Asset> {
  const body = new FormData()
  body.append('image', image, image.name)
  const response = await fetch(`/api/assets/${encodeURIComponent(id)}/thumbnail`, { method: 'POST', credentials: 'same-origin', headers: { Accept: 'application/json' }, body })
  if (!response.ok) throw await apiErrorFromResponse(response)
  const asset = assetFromJson(await response.json())
  if (!asset) throw new ApiError(500)
  return asset
}

export function assetThumbnailUrl(id: string): string {
  return `/api/assets/${encodeURIComponent(id)}/thumbnail`
}

export function assetDownloadUrl(id: string): string {
  return `/api/assets/${encodeURIComponent(id)}/download`
}

export async function issueHelperPairingCode(): Promise<HelperPairingCode> {
  const pairingCode = helperPairingCodeFromJson(await requestMutation('/api/helper/pairing-codes', 'POST'))
  if (!pairingCode) throw new ApiError(500)
  return pairingCode
}

export async function getHelperDevices(): Promise<HelperDevice[]> {
  const payload = await request('/api/helper/devices')
  if (!isObject(payload) || !Array.isArray(payload.items)) throw new ApiError(500)
  return payload.items.flatMap((item) => {
    const device = helperDeviceFromJson(item)
    return device ? [device] : []
  })
}

export async function revokeHelperDevice(deviceId: string): Promise<void> {
  await requestMutation(`/api/helper/devices/${encodeURIComponent(deviceId)}`, 'DELETE')
}
