import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './i18n'
import { ApiError, archiveAsset, assetDownloadUrl, assignProjectAsset, createProject, createProjectFolder, createTag, deleteAsset, getAppearancePreference, getAsset, getAssets, getCurrentUser, getLibraries, getProjects, getTags, removeProjectAsset, restoreAsset, setAppearancePreference, setAssetTags, uploadAssetThumbnail, uploadFiles, type Asset, type Library, type Project, type ProjectFolder, type Tag, type UserRole } from './api'
import { ModelViewer } from './features/viewer/ModelViewer'
import { AssetThumbnail } from './features/viewer/AssetThumbnail'
import type { ViewerSource } from './features/viewer/viewerSource'
import { applyTheme, readThemePreference, saveThemePreference, type ThemePreference } from './theme'

const appearanceOptions: ThemePreference[] = ['dark', 'light', 'system']
const EXPLORER_LOCATION_STORAGE_KEY = 'printvault.explorer-location'
type AuthState = 'loading' | 'authenticated' | 'unauthenticated' | 'denied' | 'error'
type AssetState = 'loading' | 'ready' | 'error'
type SelectionState = 'idle' | 'loading' | 'ready' | 'error'

type ExplorerLocation = {
  libraryKey: string | null
  projectId: string | null
  folderId: string | null
  showProjects: boolean
}

function readExplorerLocation(): ExplorerLocation {
  try {
    const saved = JSON.parse(localStorage.getItem(EXPLORER_LOCATION_STORAGE_KEY) ?? '') as Partial<ExplorerLocation>
    return {
      libraryKey: typeof saved.libraryKey === 'string' ? saved.libraryKey : null,
      projectId: typeof saved.projectId === 'string' ? saved.projectId : null,
      folderId: typeof saved.folderId === 'string' ? saved.folderId : null,
      showProjects: saved.showProjects === true,
    }
  } catch { return { libraryKey: null, projectId: null, folderId: null, showProjects: false } }
}

function explorerLocationFromHistoryState(state: unknown): ExplorerLocation | null {
  if (!state || typeof state !== 'object') return null
  const record = state as { printvaultExplorer?: unknown, location?: unknown }
  if (record.printvaultExplorer !== true || !record.location || typeof record.location !== 'object') return null
  const location = record.location as Partial<ExplorerLocation>
  return {
    libraryKey: typeof location.libraryKey === 'string' ? location.libraryKey : null,
    projectId: typeof location.projectId === 'string' ? location.projectId : null,
    folderId: typeof location.folderId === 'string' ? location.folderId : null,
    showProjects: location.showProjects === true,
  }
}

function SearchIcon() {
  return <svg aria-hidden="true" fill="none" height="17" viewBox="0 0 24 24" width="17"><circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.8" /><path d="m16 16 4.2 4.2" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></svg>
}

function MenuIcon() {
  return <svg aria-hidden="true" fill="none" height="20" viewBox="0 0 24 24" width="20"><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></svg>
}

function DetailsIcon() {
  return <svg aria-hidden="true" fill="none" height="20" viewBox="0 0 24 24" width="20"><rect height="16" rx="2" stroke="currentColor" strokeWidth="1.7" width="14" x="5" y="4" /><path d="M9 9h6M9 13h6M9 17h3" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" /></svg>
}

function CubeIcon() {
  return <svg aria-hidden="true" fill="none" height="17" viewBox="0 0 24 24" width="17"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.5" /><path d="M4.5 7.8 12 12l7.5-4.2M12 12v9" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.5" /></svg>
}

function FolderIcon() {
  return <svg aria-hidden="true" fill="none" height="28" viewBox="0 0 32 28" width="32"><path d="M3 6.5c0-1.7 1.3-3 3-3h7l2.7 3H26c1.7 0 3 1.3 3 3V23c0 1.7-1.3 3-3 3H6c-1.7 0-3-1.3-3-3V6.5Z" fill="currentColor" opacity=".9" /><path d="M3.8 10.5h24.4" stroke="var(--canvas)" strokeWidth="1.4" /></svg>
}

function assetViewerSource(asset: Asset): ViewerSource {
  return {
    kind: 'authenticated-api',
    url: assetDownloadUrl(asset.id),
    format: asset.format.toLowerCase() as ViewerSource['format'],
    ...(asset.byteSize === undefined ? {} : { byteSize: asset.byteSize }),
  }
}

function byteSizeInMegabytes(byteSize: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(byteSize / (1024 * 1024))
}

function humanReadableText(value: string): string {
  let decoded = value
  for (let index = 0; index < 3; index += 1) {
    const withBreaks = decoded
      .replace(/<\s*br\s*\/?\s*>/gi, '\n')
      .replace(/<\s*\/?\s*(?:p|div|li|h[1-6])\b[^>]*>/gi, '\n')
    const next = new DOMParser().parseFromString(withBreaks, 'text/html').body.textContent ?? decoded
    if (next === decoded) break
    decoded = next
  }
  return decoded.replace(/\u00a0/g, ' ').split('\n').map((line) => line.trim()).filter(Boolean).join('\n')
}

function threeMfCore(asset: Asset): Array<[string, string]> {
  const packageMetadata = asset.metadata.three_mf
  if (!packageMetadata || typeof packageMetadata !== 'object') return []
  const core = (packageMetadata as Record<string, unknown>).core
  if (!core || typeof core !== 'object') return []
  return Object.entries(core as Record<string, unknown>).flatMap(([key, value]) => typeof value === 'string' ? [[key, value] as [string, string]] : [])
}

function threeMfDocuments(asset: Asset): Array<{ label: string, text?: string }> {
  const packageMetadata = asset.metadata.three_mf
  if (!packageMetadata || typeof packageMetadata !== 'object') return []
  const documents = (packageMetadata as Record<string, unknown>).documents
  if (!Array.isArray(documents)) return []
  return documents.flatMap((document) => {
    if (!document || typeof document !== 'object') return []
    const item = document as Record<string, unknown>
    return typeof item.label === 'string' ? [{ label: item.label, ...(typeof item.text === 'string' ? { text: item.text } : {}) }] : []
  })
}

function FolderPicker({ disabled = false, folders, label, onChange, value }: { disabled?: boolean, folders: ProjectFolder[], label: string, onChange: (id: string | null) => void, value: string | null }) {
  const [open, setOpen] = useState(false)
  const selected = folders.find((folder) => folder.id === value)
  const choose = (id: string | null) => { onChange(id); setOpen(false) }
  return <div className="folder-picker"><button aria-expanded={open} aria-haspopup="listbox" aria-label={label} className="folder-picker-trigger" disabled={disabled} onClick={() => setOpen((current) => !current)} type="button"><span>{selected?.name ?? label}</span><span aria-hidden="true">⌄</span></button>{open && <div aria-label={label} className="folder-picker-menu" role="listbox"><button aria-selected={value === null} onClick={() => choose(null)} role="option" type="button">{label}</button>{folders.map((folder) => <button aria-selected={folder.id === value} key={folder.id} onClick={() => choose(folder.id)} role="option" type="button">{folder.parentId ? '↳ ' : ''}{folder.name}</button>)}</div>}</div>
}

function ProjectPicker({ assignedProjectIds, disabled = false, emptyLabel, label, onAssign, projects, searchLabel }: { assignedProjectIds: Set<string>, disabled?: boolean, emptyLabel: string, label: string, onAssign: (projectId: string) => void, projects: Project[], searchLabel: string }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const matches = useMemo(() => {
    const term = query.trim().toLocaleLowerCase()
    return projects.filter((project) => !assignedProjectIds.has(project.id) && (!term || project.name.toLocaleLowerCase().includes(term))).slice(0, 30)
  }, [assignedProjectIds, projects, query])
  const choose = (projectId: string) => { onAssign(projectId); setOpen(false); setQuery('') }
  return <div className="project-picker"><button aria-expanded={open} aria-haspopup="listbox" aria-label={label} className="project-picker-trigger" disabled={disabled} onClick={() => setOpen((current) => !current)} type="button">{label}<span aria-hidden="true">⌄</span></button>{open && <div aria-label={label} className="project-picker-menu" role="listbox"><input aria-label={searchLabel} autoFocus onChange={(event) => setQuery(event.target.value)} placeholder={searchLabel} type="search" value={query} />{matches.length === 0 ? <p>{emptyLabel}</p> : matches.map((project) => <button key={project.id} onClick={() => choose(project.id)} role="option" type="button">{project.name}</button>)}</div>}</div>
}

function FilterPicker({ emptyLabel, items, label, onToggle, searchLabel, selected }: { emptyLabel: string, items: Array<{ id: string, name: string }>, label: string, onToggle: (id: string) => void, searchLabel: string, selected: string[] }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const matches = useMemo(() => { const term = query.trim().toLocaleLowerCase(); return items.filter((item) => !term || item.name.toLocaleLowerCase().includes(term)).slice(0, 30) }, [items, query])
  const selectedItems = selected.flatMap((id) => { const item = items.find((candidate) => candidate.id === id); return item ? [item] : [] })
  return <div className="filter-picker"><button aria-expanded={open} aria-haspopup="listbox" className="project-picker-trigger" onClick={() => setOpen((current) => !current)} type="button">{label}{selected.length > 0 && ` (${selected.length})`}<span aria-hidden="true">⌄</span></button>{open && <div aria-label={label} className="project-picker-menu" role="listbox"><input aria-label={searchLabel} autoFocus onChange={(event) => setQuery(event.target.value)} placeholder={searchLabel} type="search" value={query} />{matches.length === 0 ? <p>{emptyLabel}</p> : matches.map((item) => <button aria-selected={selected.includes(item.id)} key={item.id} onClick={() => onToggle(item.id)} role="option" type="button">{item.name}</button>)}</div>}{selectedItems.length > 0 && <div className="filter-selected">{selectedItems.map((item) => <button aria-label={`${label}: ${item.name}`} key={item.id} onClick={() => onToggle(item.id)} type="button">{item.name} ×</button>)}</div>}</div>
}

export default function App() {
  const { t } = useTranslation()
  const initialExplorerLocation = useMemo(readExplorerLocation, [])
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference)
  const [authState, setAuthState] = useState<AuthState>('loading')
  const [role, setRole] = useState<UserRole | null>(null)
  const [assetState, setAssetState] = useState<AssetState>('loading')
  const [libraries, setLibraries] = useState<Library[]>([])
  const [assets, setAssets] = useState<Asset[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [projectFormOpen, setProjectFormOpen] = useState(false)
  const [tagFormOpen, setTagFormOpen] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [folderName, setFolderName] = useState('')
  const [folderParentId, setFolderParentId] = useState<string | null>(null)
  const [folderMessage, setFolderMessage] = useState<string | null>(null)
  const [projectMessage, setProjectMessage] = useState<string | null>(null)
  const [tagKey, setTagKey] = useState('')
  const [tagName, setTagName] = useState('')
  const [selectedTagKeys, setSelectedTagKeys] = useState<string[]>([])
  const [libraryProjectFilters, setLibraryProjectFilters] = useState<string[]>([])
  const [libraryTagFilters, setLibraryTagFilters] = useState<string[]>([])
  const [projectMutationId, setProjectMutationId] = useState<string | null>(null)
  const [folderProjectId, setFolderProjectId] = useState<string | null>(null)
  const [activeLibrary, setActiveLibrary] = useState<string | null>(initialExplorerLocation.libraryKey)
  const [activeProject, setActiveProject] = useState<string | null>(initialExplorerLocation.projectId)
  const [activeFolder, setActiveFolder] = useState<string | null>(initialExplorerLocation.folderId)
  const [showProjects, setShowProjects] = useState(initialExplorerLocation.showProjects)
  const [search, setSearch] = useState('')
  const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null)
  const [selectionState, setSelectionState] = useState<SelectionState>('idle')
  const [uploading, setUploading] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false)

  const [thumbnailRevision, setThumbnailRevision] = useState(0)
  const [uploadMessage, setUploadMessage] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const thumbnailInput = useRef<HTMLInputElement>(null)
  const appearanceMutation = useRef(0)

  const loadWorkspace = () => {
    let cancelled = false
    setAuthState('loading')
    setAssetState('loading')
    setSelectedAsset(null)
    setSelectionState('idle')

    void getCurrentUser()
      .then(async (user) => {
        if (cancelled) return
        setRole(user.role)
        setAuthState('authenticated')
        void getAppearancePreference()
          .then((serverPreference) => {
            if (cancelled || appearanceMutation.current !== 0) return
            saveThemePreference(serverPreference)
            setPreference(serverPreference)
          })
          .catch(() => undefined)
        try {
          const [nextLibraries, nextAssets, nextProjects, nextTags] = await Promise.all([getLibraries(), getAssets(), getProjects(), getTags()])
          if (cancelled) return
          setLibraries(nextLibraries.filter((library) => library.key !== 'projects'))
          if (activeLibrary === 'projects') setActiveLibrary(null)
          setAssets(nextAssets)
          setProjects(nextProjects)
          setTags(nextTags)
          setAssetState('ready')
        } catch {
          if (!cancelled) setAssetState('error')
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (error instanceof ApiError && error.status === 401) setAuthState('unauthenticated')
        else if (error instanceof ApiError && error.status === 403) setAuthState('denied')
        else setAuthState('error')
      })

    return () => { cancelled = true }
  }

  useEffect(() => loadWorkspace(), [])

  useEffect(() => {
    document.title = t('app.name')
  }, [t])

  useEffect(() => {
    localStorage.setItem(EXPLORER_LOCATION_STORAGE_KEY, JSON.stringify({ libraryKey: activeLibrary, projectId: activeProject, folderId: activeFolder, showProjects }))
  }, [activeFolder, activeLibrary, activeProject, showProjects])

  useEffect(() => {
    window.history.replaceState({ printvaultExplorer: true, location: initialExplorerLocation }, '', window.location.href)
    const onPopState = (event: PopStateEvent) => {
      const location = explorerLocationFromHistoryState(event.state)
      if (!location) return
      setActiveLibrary(location.libraryKey)
      setActiveProject(location.projectId)
      setActiveFolder(location.folderId)
      setShowProjects(location.showProjects)
      setSelectedAsset(null)
      setSelectionState('idle')
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [initialExplorerLocation])

  useEffect(() => {
    applyTheme(preference)
    if (preference !== 'system') return undefined
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const updateSystemTheme = () => applyTheme('system')
    mediaQuery.addEventListener('change', updateSystemTheme)
    return () => mediaQuery.removeEventListener('change', updateSystemTheme)
  }, [preference])

  useEffect(() => {
    if (!mobileSidebarOpen && !mobileInspectorOpen) return undefined
    const closeMobilePanels = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setMobileSidebarOpen(false)
      setMobileInspectorOpen(false)
    }
    window.addEventListener('keydown', closeMobilePanels)
    return () => window.removeEventListener('keydown', closeMobilePanels)
  }, [mobileInspectorOpen, mobileSidebarOpen])

  const selectAppearance = (nextPreference: ThemePreference) => {
    const previousPreference = preference
    const mutation = appearanceMutation.current + 1
    appearanceMutation.current = mutation
    saveThemePreference(nextPreference)
    setPreference(nextPreference)
    void setAppearancePreference(nextPreference)
      .then((serverPreference) => {
        if (appearanceMutation.current !== mutation || serverPreference === nextPreference) return
        saveThemePreference(serverPreference)
        setPreference(serverPreference)
      })
      .catch(() => {
        if (appearanceMutation.current !== mutation) return
        saveThemePreference(previousPreference)
        setPreference(previousPreference)
      })
  }

  const activeProjectRecord = useMemo(() => projects.find((project) => project.id === activeProject) ?? null, [activeProject, projects])
  const activeProjectAssetIds = useMemo(() => new Set(activeProjectRecord?.assetIds ?? []), [activeProjectRecord])
  const currentFolder = useMemo(() => activeProjectRecord?.folders.find((folder) => folder.id === activeFolder) ?? null, [activeFolder, activeProjectRecord])
  const childFolders = useMemo(() => activeProjectRecord?.folders.filter((folder) => folder.parentId === activeFolder) ?? [], [activeFolder, activeProjectRecord])
  const folderBreadcrumbs = useMemo(() => {
    if (!activeProjectRecord || !activeFolder) return []
    const byId = new Map(activeProjectRecord.folders.map((folder) => [folder.id, folder]))
    const result: ProjectFolder[] = []
    let folder = byId.get(activeFolder)
    while (folder) { result.unshift(folder); folder = folder.parentId ? byId.get(folder.parentId) : undefined }
    return result
  }, [activeFolder, activeProjectRecord])

  const visibleAssets = useMemo(() => {
    const term = search.trim().toLocaleLowerCase()
    return assets.filter((asset) => {
      if (activeLibrary && asset.libraryKey !== activeLibrary) return false
      if (activeProject && (!activeProjectAssetIds.has(asset.id) || (activeProjectRecord?.assetFolderIds[asset.id] ?? null) !== activeFolder)) return false
      if (!activeProject && activeLibrary === null && libraryProjectFilters.length > 0 && !projects.some((project) => libraryProjectFilters.includes(project.id) && project.assetIds.includes(asset.id))) return false
      if (!activeProject && activeLibrary === null && libraryTagFilters.length > 0 && !asset.tags.some((tag) => libraryTagFilters.includes(tag))) return false
      if (!term) return true
      return [asset.filename, asset.relativePath, ...asset.tags].some((value) => value.toLocaleLowerCase().includes(term))
    })
  }, [activeFolder, activeLibrary, activeProject, activeProjectAssetIds, activeProjectRecord, assets, libraryProjectFilters, libraryTagFilters, projects, search])

  const canUpload = role === 'editor' || role === 'admin'
  const uploadLibrary = activeLibrary && activeLibrary !== 'archive'
    ? activeLibrary
    : libraries.find((library) => library.key === 'models')?.key ?? libraries.find((library) => library.key !== 'archive')?.key ?? null

  const navigateExplorer = (location: ExplorerLocation) => {
    window.history.pushState({ printvaultExplorer: true, location }, '', window.location.href)
    setActiveLibrary(location.libraryKey)
    setActiveProject(location.projectId)
    setActiveFolder(location.folderId)
    setShowProjects(location.showProjects)
  }

  const handleUpload = async (incoming: FileList | File[]) => {
    const files = Array.from(incoming)
    if (!canUpload || !uploadLibrary || files.length === 0 || uploading) return
    setUploading(true)
    setUploadMessage(null)
    try {
      const result = await uploadFiles(uploadLibrary, files)
      setAssets((current) => [...current.filter((asset) => !result.items.some((uploaded) => uploaded.id === asset.id)), ...result.items])
      setUploadMessage(result.rejected.length === 0
        ? t('upload.success', { count: result.items.length })
        : t('upload.partial', { uploaded: result.items.length, rejected: result.rejected.length }))
    } catch {
      setUploadMessage(t('upload.error'))
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const chooseLibrary = async (libraryKey: string | null) => {
    setMobileSidebarOpen(false)
    navigateExplorer({ libraryKey, projectId: null, folderId: null, showProjects: false })
    setSelectedAsset(null)
    setSelectionState('idle')
    setAssetState('loading')
    try {
      setAssets(await getAssets(libraryKey))
      setAssetState('ready')
    } catch { setAssetState('error') }
  }

  const chooseProject = async (projectId: string) => {
    setMobileSidebarOpen(false)
    navigateExplorer({ libraryKey: null, projectId, folderId: null, showProjects: false })
    setSelectedAsset(null)
    setSelectionState('idle')
    setAssetState('loading')
    try {
      setAssets(await getAssets())
      setAssetState('ready')
    } catch {
      setAssetState('error')
    }
  }

  const chooseProjects = () => {
    setMobileSidebarOpen(false)
    navigateExplorer({ libraryKey: null, projectId: null, folderId: null, showProjects: true })
    setSelectedAsset(null)
    setSelectionState('idle')
  }

  const chooseFolder = (folderId: string | null) => {
    setMobileSidebarOpen(false)
    if (!activeProject) return
    navigateExplorer({ libraryKey: null, projectId: activeProject, folderId, showProjects: false })
    setSelectedAsset(null)
    setSelectionState('idle')
  }

  const submitProject = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      const project = await createProject(projectName.trim(), projectDescription.trim())
      setProjects((current) => [...current, project].sort((left, right) => left.name.localeCompare(right.name)))
      setProjectName('')
      setProjectDescription('')
      setProjectFormOpen(false)
    } catch { setAssetState('error') }
  }

  const submitProjectFolder = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!activeProject || !folderName.trim()) return
    try {
      const folder = await createProjectFolder(activeProject, folderName.trim(), folderParentId)
      setProjects((current) => current.map((project) => project.id === activeProject ? { ...project, folders: [...project.folders, folder] } : project))
      setFolderName('')
      setFolderParentId(null)
      setFolderMessage(t('projects.folderCreated'))
    } catch { setFolderMessage(t('projects.folderCreateFailed')) }
  }

  const submitTag = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    try {
      const tag = await createTag(tagKey.trim(), tagName.trim())
      setTags((current) => [...current, tag].sort((left, right) => left.name.localeCompare(right.name)))
      setSelectedTagKeys((current) => [...new Set([...current, tag.key])])
      setTagKey('')
      setTagName('')
      setTagFormOpen(false)
    } catch { setSelectionState('error') }
  }

  const saveSelectedTags = async () => {
    if (!selectedAsset) return
    try {
      const updated = await setAssetTags(selectedAsset.id, selectedTagKeys)
      setAssets((current) => current.map((asset) => asset.id === updated.id ? updated : asset))
      setSelectedAsset(updated)
    } catch { setSelectionState('error') }
  }

  const assignSelectedProject = async (projectId: string, folderId: string | null = null) => {
    if (!selectedAsset || !projectId || projectMutationId) return
    setProjectMutationId(projectId)
    try {
      const updated = await assignProjectAsset(projectId, selectedAsset.id, folderId)
      setProjects((current) => current.map((project) => project.id === updated.id ? updated : project))
      setProjectMessage(t('projects.assigned', { asset: selectedAsset.filename, project: updated.name }))
    } catch { setProjectMessage(t('projects.assignFailed')) }
    finally { setProjectMutationId(null) }
  }

  const removeSelectedProject = async (projectId: string) => {
    if (!selectedAsset || !projectId || projectMutationId) return
    setProjectMutationId(projectId)
    try {
      const updated = await removeProjectAsset(projectId, selectedAsset.id)
      setProjects((current) => current.map((project) => project.id === updated.id ? updated : project))
      setProjectMessage(t('projects.removed', { asset: selectedAsset.filename, project: updated.name }))
    } catch { setProjectMessage(t('projects.removeFailed')) }
    finally { setProjectMutationId(null) }
  }

  const selectAsset = async (id: string) => {
    setSelectionState('loading')
    setSelectedAsset(null)
    try {
      const asset = await getAsset(id)
      setSelectedAsset(asset)
      setSelectedTagKeys(asset.tags)
      setSelectionState('ready')
      if (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 760px)').matches) setMobileInspectorOpen(true)
    } catch {
      setSelectionState('error')
    }
  }

  const archiveSelectedAsset = async () => {
    if (!selectedAsset || !window.confirm(t('actions.archiveConfirm', { name: selectedAsset.filename }))) return
    try {
      const archived = await archiveAsset(selectedAsset.id)
      setAssets((current) => current.map((asset) => asset.id === archived.id ? archived : asset))
      setSelectedAsset(archived)
    } catch { setSelectionState('error') }
  }

  const restoreSelectedAsset = async () => {
    if (!selectedAsset || !window.confirm(t('actions.restoreConfirm', { name: selectedAsset.filename }))) return
    try {
      const restored = await restoreAsset(selectedAsset.id)
      setAssets((current) => current.filter((asset) => asset.id !== restored.id))
      setSelectedAsset(restored)
    } catch { setSelectionState('error') }
  }

  const uploadSelectedThumbnail = async (files: FileList | null) => {
    const image = files?.item(0)
    if (!selectedAsset || !image) return
    try {
      const updated = await uploadAssetThumbnail(selectedAsset.id, image)
      setAssets((current) => current.map((asset) => asset.id === updated.id ? updated : asset))
      setSelectedAsset(updated)
      setThumbnailRevision((current) => current + 1)
    } catch {
      setSelectionState('error')
    } finally {
      if (thumbnailInput.current) thumbnailInput.current.value = ''
    }
  }


  const deleteSelectedAsset = async () => {
    if (!selectedAsset || !window.confirm(t('actions.deleteConfirm', { name: selectedAsset.filename }))) return
    try {
      await deleteAsset(selectedAsset.id)
      setAssets((current) => current.filter((asset) => asset.id !== selectedAsset.id))
      setSelectedAsset(null)
      setSelectionState('idle')
    } catch { setSelectionState('error') }
  }

  const uploadDropzone = !showProjects && canUpload && uploadLibrary && <div aria-label={t('upload.dropLabel')} className={`upload-dropzone ${isDragging ? 'is-dragging' : ''}`} onClick={() => fileInput.current?.click()} onDragEnter={(event) => { event.preventDefault(); setIsDragging(true) }} onDragLeave={(event) => { event.preventDefault(); setIsDragging(false) }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); setIsDragging(false); void handleUpload(event.dataTransfer.files) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fileInput.current?.click() } }} role="button" tabIndex={0}>
    <input accept=".stl,.obj,.3mf" aria-label={t('upload.inputLabel')} className="visually-hidden" multiple onChange={(event) => void handleUpload(event.currentTarget.files ?? [])} ref={fileInput} type="file" />
    <strong>{uploading ? t('upload.uploading') : t('upload.title')}</strong><span>{t('upload.description')}</span>
  </div>

  if (authState !== 'authenticated') {
    const titleKey = authState === 'unauthenticated'
      ? 'auth.signInTitle'
      : authState === 'denied'
        ? 'auth.accessDeniedTitle'
        : authState === 'error'
          ? 'auth.sessionErrorTitle'
          : 'auth.loading'
    const descriptionKey = authState === 'unauthenticated'
      ? 'auth.signInDescription'
      : authState === 'denied'
        ? 'auth.accessDeniedDescription'
        : authState === 'error'
          ? 'auth.sessionErrorDescription'
          : null

    return (
      <main className="auth-screen">
        <div className="auth-card">
          <div className="brand"><div className="brand-mark"><CubeIcon /></div><span className="brand-name">{t('app.name')}</span></div>
          <h1>{t(titleKey)}</h1>
          {descriptionKey && <p className="inspector-description">{t(descriptionKey)}</p>}
          {(authState === 'unauthenticated' || authState === 'denied') && <a className="primary-button" href="/api/auth/login">{t(authState === 'denied' ? 'auth.signInAgain' : 'auth.signIn')}</a>}
          {authState === 'error' && <button className="primary-button" onClick={loadWorkspace} type="button">{t('auth.retry')}</button>}
        </div>
      </main>
    )
  }

  return (
    <div className="app-shell">
      <aside aria-label={t('navigation.libraries')} aria-modal={mobileSidebarOpen || undefined} className={`sidebar ${mobileSidebarOpen ? 'is-mobile-open' : ''}`} role={mobileSidebarOpen ? 'dialog' : undefined}>
        <div className="brand">
          <div className="brand-mark"><CubeIcon /></div>
          <div className="brand-copy"><span className="brand-name">{t('app.name')}</span><span className="brand-tagline">{t('app.tagline')}</span></div>
          <button aria-label={t('actions.close')} className="mobile-panel-close" onClick={() => setMobileSidebarOpen(false)} type="button">×</button>
        </div>

        <nav aria-label={t('navigation.libraries')}>
          <p className="nav-label">{t('navigation.libraries')}</p>
          <div className="library-nav">
            <button className={`nav-item ${activeLibrary === null ? 'is-active' : ''}`} onClick={() => void chooseLibrary(null)} type="button"><span className="nav-bullet" />{t('navigation.allAssets')}</button>
            {libraries.map((library) => (
              <button className={`nav-item ${activeLibrary === library.key ? 'is-active' : ''}`} key={library.key} onClick={() => void chooseLibrary(library.key)} type="button"><span className="nav-bullet" />{library.name}</button>
            ))}
          </div>
        </nav>

        <nav aria-label={t('projects.title')} className="projects-nav">
          <div className="nav-section-heading"><button className="nav-label nav-section-button" onClick={chooseProjects} type="button">{t('projects.title')}</button>{canUpload && <button aria-label={t('projects.add')} className="nav-add-button" onClick={() => { setProjectFormOpen(true); setMobileSidebarOpen(false) }} type="button">+</button>}</div>
          <div className="library-nav">{projects.slice(0, 30).map((project) => <button className={`nav-item ${activeProject === project.id ? 'is-active' : ''}`} key={project.id} onClick={() => void chooseProject(project.id)} type="button"><span className="nav-bullet" />{project.name}<span className="nav-count">{project.assetIds.length}</span></button>)}{projects.length > 30 && <ProjectPicker assignedProjectIds={new Set()} label={t('projects.open')} onAssign={(projectId) => void chooseProject(projectId)} projects={projects} searchLabel={t('projects.search')} emptyLabel={t('projects.noMatches')} />}</div>
        </nav>

        <fieldset className="appearance sidebar-footer">
          <legend className="nav-label">{t('appearance.label')}</legend>
          <div className="theme-options">
            {appearanceOptions.map((option) => {
              const controlId = `appearance-${option}`
              return <div className="theme-option" key={option}><input checked={preference === option} id={controlId} name="appearance" onChange={() => selectAppearance(option)} type="radio" /><label htmlFor={controlId}>{t(`appearance.${option}`)}</label></div>
            })}
          </div>
        </fieldset>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <button aria-expanded={mobileSidebarOpen} aria-label={t('topbar.menu')} className="icon-button" onClick={() => { setMobileSidebarOpen(true); setMobileInspectorOpen(false) }} type="button"><MenuIcon /></button>
          <label className="search"><span className="search-icon"><SearchIcon /></span><input aria-label={t('topbar.searchLabel')} onChange={(event) => setSearch(event.target.value)} placeholder={t('topbar.searchPlaceholder')} type="search" value={search} /></label>
          <button aria-expanded={mobileInspectorOpen} aria-label={t('topbar.inspector')} className="icon-button" onClick={() => { setMobileInspectorOpen(true); setMobileSidebarOpen(false) }} type="button"><DetailsIcon /></button>
        </header>

        <section aria-label={t('content.title')} className="content">
          <div className="content-header"><div>{activeProjectRecord ? <><p className="section-label">{activeProjectRecord.name}</p><div className="folder-breadcrumbs"><button onClick={() => chooseFolder(null)} type="button">{activeProjectRecord.name}</button>{folderBreadcrumbs.map((folder) => <span className="folder-breadcrumb-segment" key={folder.id}><span>/</span><button onClick={() => chooseFolder(folder.id)} type="button">{folder.name}</button></span>)}</div><h1>{currentFolder?.name ?? activeProjectRecord.name}</h1></> : <><p className="section-label">{t('content.eyebrow')}</p><h1>{t('content.title')}</h1></>}{assetState === 'ready' && <p className="result-count">{t('content.resultCount', { count: visibleAssets.length })}</p>}</div></div>
          {!activeProject && activeLibrary === null && !showProjects && <div className="library-workbench"><section aria-label={t('projects.filter')} className="library-filters"><FilterPicker emptyLabel={t('projects.noMatches')} items={projects.map((project) => ({ id: project.id, name: project.name }))} label={t('projects.filter')} onToggle={(id) => setLibraryProjectFilters((current) => current.includes(id) ? current.filter((projectId) => projectId !== id) : [...current, id])} searchLabel={t('projects.search')} selected={libraryProjectFilters} /><FilterPicker emptyLabel={t('tags.noMatches')} items={tags.map((tag) => ({ id: tag.key, name: tag.name }))} label={t('tags.filter')} onToggle={(id) => setLibraryTagFilters((current) => current.includes(id) ? current.filter((tagKey) => tagKey !== id) : [...current, id])} searchLabel={t('tags.search')} selected={libraryTagFilters} /></section>{uploadDropzone}</div>}

          {activeProject && canUpload && <section className="project-folders"><h2>{t('projects.folder')}</h2><form className="project-folder-form" onSubmit={submitProjectFolder}><label>{t('projects.folderName')}<input onChange={(event) => setFolderName(event.target.value)} required value={folderName} /></label><label>{t('projects.folderParent')}<FolderPicker folders={(projects.find((project) => project.id === activeProject)?.folders ?? [])} label={t('projects.folderRoot')} onChange={setFolderParentId} value={folderParentId} /></label><button className="primary-button" type="submit">{t('projects.folderCreate')}</button></form>{folderMessage && <p className="operation-message" role="status">{folderMessage}</p>}</section>}
          {(activeProject || activeLibrary !== null) && uploadDropzone}
          {uploadMessage && <p className="upload-message" role="status">{uploadMessage}</p>}
          {showProjects && <div className="project-grid">{projects.map((project) => <button className="project-card" key={project.id} onClick={() => void chooseProject(project.id)} type="button"><h2>{project.name}</h2>{project.description && <p>{project.description}</p>}<span>{t('content.resultCount', { count: project.assetIds.length })}</span></button>)}</div>}
          {!showProjects && assetState === 'loading' && <p role="status">{t('content.loading')}</p>}
          {!showProjects && assetState === 'error' && <div className="content-state" role="alert"><p>{t('content.error')}</p><button className="ghost-button" onClick={loadWorkspace} type="button">{t('content.retry')}</button></div>}
          {activeProjectRecord && childFolders.length > 0 && <div className="folder-grid">{childFolders.map((folder) => <button aria-label={folder.name} className="folder-card" key={folder.id} onClick={() => chooseFolder(folder.id)} type="button"><span aria-hidden="true" className="folder-card-icon"><FolderIcon /></span><span className="folder-card-name">{folder.name}</span></button>)}</div>}
          {assetState === 'ready' && visibleAssets.length === 0 && childFolders.length === 0 && <div className="content-state"><h2>{t('content.emptyTitle')}</h2><p>{t('content.emptyDescription')}</p></div>}
          {assetState === 'ready' && visibleAssets.length > 0 && <div aria-busy="false" className="asset-grid">{visibleAssets.map((asset) => <article className="asset-card" key={asset.id}><button aria-label={asset.filename} className="asset-card-button" onClick={() => void selectAsset(asset.id)} type="button"><div className="asset-preview"><AssetThumbnail assetId={asset.id} revision={thumbnailRevision} /></div><div className="asset-body"><h2 className="asset-name">{asset.filename}</h2><p className="asset-meta">{asset.byteSize === undefined ? t('content.assetMeta', { format: asset.format.toUpperCase(), path: asset.relativePath }) : t('content.assetMetaWithSize', { format: asset.format.toUpperCase(), path: asset.relativePath, size: t('content.fileSize', { size: byteSizeInMegabytes(asset.byteSize) }) })}</p><div className="project-badges">{projects.filter((project) => project.assetIds.includes(asset.id)).map((project) => <span className="project-badge" key={project.id}>{project.name}{project.assetFolderIds[asset.id] ? ` · ${project.folders.find((folder) => folder.id === project.assetFolderIds[asset.id])?.name ?? ''}` : ''}</span>)}</div><div className="tags">{asset.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div></div></button></article>)}</div>}
        </section>
      </main>

      {(mobileSidebarOpen || mobileInspectorOpen) && <div aria-hidden="true" className="mobile-panel-backdrop" onMouseDown={() => { setMobileSidebarOpen(false); setMobileInspectorOpen(false) }} />}
      <aside aria-label={t('inspector.label')} aria-modal={mobileInspectorOpen || undefined} className={`inspector ${mobileInspectorOpen ? 'is-mobile-open' : ''}`} role={mobileInspectorOpen ? 'dialog' : 'complementary'}>
        <div className="inspector-header"><span className="section-label">{t('inspector.label')}</span><button aria-label={t('actions.close')} className="mobile-panel-close" onClick={() => setMobileInspectorOpen(false)} type="button">×</button></div>
        {selectionState === 'idle' && <div className="content-state"><h2 className="inspector-title">{t('inspector.emptyTitle')}</h2><p>{t('inspector.emptyDescription')}</p></div>}
        {selectionState === 'loading' && <p role="status">{t('inspector.loading')}</p>}
        {selectionState === 'error' && <p role="alert">{t('inspector.error')}</p>}
        {selectedAsset && <><h2 className="inspector-title">{selectedAsset.filename}</h2><a className="primary-button full-width inspector-download" href={assetDownloadUrl(selectedAsset.id)}>{t('inspector.download')}</a><ModelViewer source={assetViewerSource(selectedAsset)} /><div className="tags">{selectedAsset.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div><div className="stats"><div className="stat"><span className="stat-label">{t('inspector.format')}</span><span className="stat-value">{selectedAsset.format.toUpperCase()}</span></div><div className="stat"><span className="stat-label">{t('inspector.path')}</span><span className="stat-value">{selectedAsset.relativePath}</span></div>{selectedAsset.byteSize !== undefined && <div className="stat"><span className="stat-label">{t('inspector.size')}</span><span className="stat-value">{t('content.fileSize', { size: byteSizeInMegabytes(selectedAsset.byteSize) })}</span></div>}</div>{threeMfCore(selectedAsset).length > 0 && <section className="asset-info"><h3>{t('metadata.title')}</h3>{threeMfCore(selectedAsset).map(([key, value]) => key === 'description' ? <div className="asset-description" key={key}><strong>{key}:</strong>{humanReadableText(value).split('\n').map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div> : <p key={key}><strong>{key}:</strong> {value}</p>)}</section>}{threeMfDocuments(selectedAsset).length > 0 && <section className="asset-info"><h3>{t('metadata.instructions')}</h3>{threeMfDocuments(selectedAsset).map((document) => <details key={document.label}><summary>{document.label}</summary>{document.text ? <pre>{humanReadableText(document.text)}</pre> : <p>{t('metadata.binaryDocument')}</p>}</details>)}</section>}<section className="asset-management">{canUpload && <><div aria-label={t('projects.assign')} className="project-picker"><span>{t('projects.assign')}</span><ProjectPicker assignedProjectIds={new Set(projects.filter((project) => project.assetIds.includes(selectedAsset.id)).map((project) => project.id))} disabled={projectMutationId !== null} emptyLabel={t('projects.noMatches')} label={t('projects.assign')} onAssign={(projectId) => void assignSelectedProject(projectId)} projects={projects} searchLabel={t('projects.search')} /><div className="assigned-projects">{projects.filter((project) => project.assetIds.includes(selectedAsset.id)).map((project) => { const folderId = project.assetFolderIds[selectedAsset.id] ?? null; return <div className="assigned-project" key={project.id}><button aria-expanded={folderProjectId === project.id} aria-label={`${t('projects.folder')} ${project.name}`} className="assigned-project-name" onClick={() => setFolderProjectId((current) => current === project.id ? null : project.id)} type="button">{project.name}</button><button aria-label={`${project.name} ${t('projects.remove')}`} className="project-remove" disabled={projectMutationId === project.id} onClick={() => void removeSelectedProject(project.id)} type="button">×</button>{folderProjectId === project.id && <FolderPicker disabled={projectMutationId === project.id} folders={project.folders} label={t('projects.folderRoot')} onChange={(nextFolderId) => void assignSelectedProject(project.id, nextFolderId)} value={folderId} />}</div>})}</div>{projectMessage && <p className="operation-message" role="status">{projectMessage}</p>}</div><div className="tag-management"><div className="management-heading"><h3>{t('tags.create')}</h3><button className="ghost-button" onClick={() => setTagFormOpen(true)} type="button">{t('tags.create')}</button></div>{tags.map((tag) => <label className="tag-option" key={tag.key}><input checked={selectedTagKeys.includes(tag.key)} onChange={(event) => setSelectedTagKeys((current) => event.target.checked ? [...new Set([...current, tag.key])] : current.filter((key) => key !== tag.key))} type="checkbox" />{tag.name}</label>)}{tags.length > 0 && <button className="ghost-button full-width" onClick={() => void saveSelectedTags()} type="button">{t('tags.save')}</button>}</div></>}</section>{canUpload && <section className="thumbnail-upload"><h3>{t('thumbnail.upload')}</h3><input accept="image/png,image/jpeg,image/webp" aria-label={t('thumbnail.upload')} className="visually-hidden" onChange={(event) => void uploadSelectedThumbnail(event.currentTarget.files)} ref={thumbnailInput} type="file" /><button className="ghost-button full-width" onClick={() => thumbnailInput.current?.click()} type="button">{t('thumbnail.upload')}</button></section>}{tagFormOpen && <form aria-label={t('tags.create')} className="inline-form" onSubmit={submitTag}><label>{t('tags.key')}<input onChange={(event) => setTagKey(event.target.value)} pattern="[a-z0-9][a-z0-9-]*" required value={tagKey} /></label><label>{t('tags.name')}<input onChange={(event) => setTagName(event.target.value)} required value={tagName} /></label><div className="form-actions"><button className="ghost-button" onClick={() => setTagFormOpen(false)} type="button">{t('actions.cancel')}</button><button className="primary-button" type="submit">{t('actions.save')}</button></div></form>}{canUpload && !selectedAsset.archived && <button className="ghost-button full-width" onClick={() => void archiveSelectedAsset()} type="button">{t('actions.archive')}</button>}{canUpload && selectedAsset.archived && <button className="ghost-button full-width" onClick={() => void restoreSelectedAsset()} type="button">{t('actions.restore')}</button>}{role === 'admin' && <button className="danger-button full-width" onClick={() => void deleteSelectedAsset()} type="button">{t('actions.delete')}</button>}</>}
      </aside>
      {projectFormOpen && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setProjectFormOpen(false) }}><form aria-labelledby="project-create-title" aria-modal="true" className="project-modal" onKeyDown={(event) => { if (event.key === 'Escape') setProjectFormOpen(false) }} onSubmit={submitProject} role="dialog"><h2 id="project-create-title">{t('projects.create')}</h2><label>{t('projects.name')}<input autoFocus onChange={(event) => setProjectName(event.target.value)} required value={projectName} /></label><label>{t('projects.description')}<textarea onChange={(event) => setProjectDescription(event.target.value)} value={projectDescription} /></label><div className="form-actions"><button className="ghost-button" onClick={() => setProjectFormOpen(false)} type="button">{t('actions.cancel')}</button><button className="primary-button" type="submit">{t('actions.save')}</button></div></form></div>}
    </div>
  )
}
