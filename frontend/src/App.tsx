import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './i18n'
import { ApiError, archiveAsset, assetDownloadUrl, assignProjectAsset, createProject, createProjectFolder, createTag, deleteAsset, getAsset, getAssets, getCurrentUser, getLibraries, getProjects, getTags, restoreAsset, setAssetTags, uploadAssetThumbnail, uploadFiles, type Asset, type Library, type Project, type Tag, type UserRole } from './api'
import { ModelViewer } from './features/viewer/ModelViewer'
import { AssetThumbnail } from './features/viewer/AssetThumbnail'
import type { ViewerSource } from './features/viewer/viewerSource'
import { applyTheme, readThemePreference, saveThemePreference, type ThemePreference } from './theme'

const appearanceOptions: ThemePreference[] = ['dark', 'light', 'system']
type AuthState = 'loading' | 'authenticated' | 'unauthenticated' | 'denied' | 'error'
type AssetState = 'loading' | 'ready' | 'error'
type SelectionState = 'idle' | 'loading' | 'ready' | 'error'

function SearchIcon() {
  return <svg aria-hidden="true" fill="none" height="17" viewBox="0 0 24 24" width="17"><circle cx="11" cy="11" r="6.5" stroke="currentColor" strokeWidth="1.8" /><path d="m16 16 4.2 4.2" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" /></svg>
}

function CubeIcon() {
  return <svg aria-hidden="true" fill="none" height="17" viewBox="0 0 24 24" width="17"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.5" /><path d="M4.5 7.8 12 12l7.5-4.2M12 12v9" stroke="currentColor" strokeLinejoin="round" strokeWidth="1.5" /></svg>
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

export default function App() {
  const { t } = useTranslation()
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
  const [activeLibrary, setActiveLibrary] = useState<string | null>(null)
  const [activeProject, setActiveProject] = useState<string | null>(null)
  const [showProjects, setShowProjects] = useState(false)
  const [search, setSearch] = useState('')
  const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null)
  const [selectionState, setSelectionState] = useState<SelectionState>('idle')
  const [uploading, setUploading] = useState(false)
  const [isDragging, setIsDragging] = useState(false)

  const [thumbnailRevision, setThumbnailRevision] = useState(0)
  const [uploadMessage, setUploadMessage] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const thumbnailInput = useRef<HTMLInputElement>(null)

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
        try {
          const [nextLibraries, nextAssets, nextProjects, nextTags] = await Promise.all([getLibraries(), getAssets(), getProjects(), getTags()])
          if (cancelled) return
          setLibraries(nextLibraries)
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
    applyTheme(preference)
    if (preference !== 'system') return undefined
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    const updateSystemTheme = () => applyTheme('system')
    mediaQuery.addEventListener('change', updateSystemTheme)
    return () => mediaQuery.removeEventListener('change', updateSystemTheme)
  }, [preference])

  const selectAppearance = (nextPreference: ThemePreference) => {
    saveThemePreference(nextPreference)
    setPreference(nextPreference)
  }

  const activeProjectAssetIds = useMemo(() => new Set(projects.find((project) => project.id === activeProject)?.assetIds ?? []), [activeProject, projects])

  const visibleAssets = useMemo(() => {
    const term = search.trim().toLocaleLowerCase()
    return assets.filter((asset) => {
      if (activeLibrary && asset.libraryKey !== activeLibrary) return false
      if (activeProject && !activeProjectAssetIds.has(asset.id)) return false
      if (!term) return true
      return [asset.filename, asset.relativePath, ...asset.tags].some((value) => value.toLocaleLowerCase().includes(term))
    })
  }, [activeLibrary, activeProject, activeProjectAssetIds, assets, search])

  const canUpload = role === 'editor' || role === 'admin'
  const uploadLibrary = activeLibrary && activeLibrary !== 'archive'
    ? activeLibrary
    : libraries.find((library) => library.key === 'models')?.key ?? libraries.find((library) => library.key !== 'archive')?.key ?? null

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
    setActiveLibrary(libraryKey)
    setActiveProject(null)
    setShowProjects(false)
    setSelectedAsset(null)
    setSelectionState('idle')
    setAssetState('loading')
    try {
      setAssets(await getAssets(libraryKey))
      setAssetState('ready')
    } catch { setAssetState('error') }
  }

  const chooseProject = async (projectId: string) => {
    setActiveProject(projectId)
    setActiveLibrary(null)
    setShowProjects(false)
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
    setActiveProject(null)
    setActiveLibrary(null)
    setShowProjects(true)
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
    if (!selectedAsset || !projectId) return
    try {
      const updated = await assignProjectAsset(projectId, selectedAsset.id, folderId)
      setProjects((current) => current.map((project) => project.id === updated.id ? updated : project))
      setProjectMessage(t('projects.assigned', { asset: selectedAsset.filename, project: updated.name }))
    } catch { setProjectMessage(t('projects.assignFailed')) }
  }

  const selectAsset = async (id: string) => {
    setSelectionState('loading')
    setSelectedAsset(null)
    try {
      const asset = await getAsset(id)
      setSelectedAsset(asset)
      setSelectedTagKeys(asset.tags)
      setSelectionState('ready')
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
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><CubeIcon /></div>
          <div className="brand-copy"><span className="brand-name">{t('app.name')}</span><span className="brand-tagline">{t('app.tagline')}</span></div>
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
          <div className="nav-section-heading"><button className="nav-label nav-section-button" onClick={chooseProjects} type="button">{t('projects.title')}</button>{canUpload && <button aria-label={t('projects.add')} className="nav-add-button" onClick={() => setProjectFormOpen(true)} type="button">+</button>}</div>
          <div className="library-nav">{projects.map((project) => <button className={`nav-item ${activeProject === project.id ? 'is-active' : ''}`} key={project.id} onClick={() => void chooseProject(project.id)} type="button"><span className="nav-bullet" />{project.name}<span className="nav-count">{project.assetIds.length}</span></button>)}</div>
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
          <label className="search"><span className="search-icon"><SearchIcon /></span><input aria-label={t('topbar.searchLabel')} onChange={(event) => setSearch(event.target.value)} placeholder={t('topbar.searchPlaceholder')} type="search" value={search} /></label>
        </header>

        <section aria-label={t('content.title')} className="content">
          <div className="content-header"><div><p className="section-label">{t('content.eyebrow')}</p><h1>{t('content.title')}</h1>{assetState === 'ready' && <p className="result-count">{t('content.resultCount', { count: visibleAssets.length })}</p>}</div>{canUpload && <button className="primary-button" onClick={() => setProjectFormOpen(true)} type="button">{t('projects.create')}</button>}</div>
          {projectFormOpen && <form aria-label={t('projects.create')} className="inline-form" onSubmit={submitProject}><label>{t('projects.name')}<input autoFocus onChange={(event) => setProjectName(event.target.value)} required value={projectName} /></label><label>{t('projects.description')}<textarea onChange={(event) => setProjectDescription(event.target.value)} value={projectDescription} /></label><div className="form-actions"><button className="ghost-button" onClick={() => setProjectFormOpen(false)} type="button">{t('actions.cancel')}</button><button className="primary-button" type="submit">{t('actions.save')}</button></div></form>}
          {activeProject && canUpload && <section className="project-folders"><h2>{t('projects.folder')}</h2><form className="project-folder-form" onSubmit={submitProjectFolder}><label>{t('projects.folderName')}<input onChange={(event) => setFolderName(event.target.value)} required value={folderName} /></label><label>{t('projects.folderParent')}<select onChange={(event) => setFolderParentId(event.target.value || null)} value={folderParentId ?? ''}><option value="">{t('projects.folderRoot')}</option>{(projects.find((project) => project.id === activeProject)?.folders ?? []).map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label><button className="primary-button" type="submit">{t('projects.folderCreate')}</button></form>{folderMessage && <p className="operation-message" role="status">{folderMessage}</p>}<div className="folder-list">{(projects.find((project) => project.id === activeProject)?.folders ?? []).map((folder) => <span className="folder-chip" key={folder.id}>{folder.parentId ? '↳ ' : ''}{folder.name}</span>)}</div></section>}
          {!showProjects && canUpload && uploadLibrary && <div aria-label={t('upload.dropLabel')} className={`upload-dropzone ${isDragging ? 'is-dragging' : ''}`} onClick={() => fileInput.current?.click()} onDragEnter={(event) => { event.preventDefault(); setIsDragging(true) }} onDragLeave={(event) => { event.preventDefault(); setIsDragging(false) }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); setIsDragging(false); void handleUpload(event.dataTransfer.files) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fileInput.current?.click() } }} role="button" tabIndex={0}>
            <input accept=".stl,.obj,.3mf" aria-label={t('upload.inputLabel')} className="visually-hidden" multiple onChange={(event) => void handleUpload(event.currentTarget.files ?? [])} ref={fileInput} type="file" />
            <strong>{uploading ? t('upload.uploading') : t('upload.title')}</strong><span>{t('upload.description')}</span>
          </div>}
          {uploadMessage && <p className="upload-message" role="status">{uploadMessage}</p>}
          {showProjects && <div className="project-grid">{projects.map((project) => <button className="project-card" key={project.id} onClick={() => void chooseProject(project.id)} type="button"><h2>{project.name}</h2>{project.description && <p>{project.description}</p>}<span>{t('content.resultCount', { count: project.assetIds.length })}</span></button>)}</div>}
          {!showProjects && assetState === 'loading' && <p role="status">{t('content.loading')}</p>}
          {!showProjects && assetState === 'error' && <div className="content-state" role="alert"><p>{t('content.error')}</p><button className="ghost-button" onClick={loadWorkspace} type="button">{t('content.retry')}</button></div>}
          {assetState === 'ready' && visibleAssets.length === 0 && <div className="content-state"><h2>{t('content.emptyTitle')}</h2><p>{t('content.emptyDescription')}</p></div>}
          {assetState === 'ready' && visibleAssets.length > 0 && <div aria-busy="false" className="asset-grid">{visibleAssets.map((asset) => <article className="asset-card" key={asset.id}><button aria-label={asset.filename} className="asset-card-button" onClick={() => void selectAsset(asset.id)} type="button"><div className="asset-preview"><AssetThumbnail assetId={asset.id} revision={thumbnailRevision} /></div><div className="asset-body"><h2 className="asset-name">{asset.filename}</h2><p className="asset-meta">{asset.byteSize === undefined ? t('content.assetMeta', { format: asset.format.toUpperCase(), path: asset.relativePath }) : t('content.assetMetaWithSize', { format: asset.format.toUpperCase(), path: asset.relativePath, size: t('content.fileSize', { size: byteSizeInMegabytes(asset.byteSize) }) })}</p><div className="project-badges">{projects.filter((project) => project.assetIds.includes(asset.id)).map((project) => <span className="project-badge" key={project.id}>{project.name}{project.assetFolderIds[asset.id] ? ` · ${project.folders.find((folder) => folder.id === project.assetFolderIds[asset.id])?.name ?? ''}` : ''}</span>)}</div><div className="tags">{asset.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div></div></button></article>)}</div>}
        </section>
      </main>

      <aside aria-label={t('inspector.label')} className="inspector" role="complementary">
        <div className="inspector-header"><span className="section-label">{t('inspector.label')}</span></div>
        {selectionState === 'idle' && <div className="content-state"><h2 className="inspector-title">{t('inspector.emptyTitle')}</h2><p>{t('inspector.emptyDescription')}</p></div>}
        {selectionState === 'loading' && <p role="status">{t('inspector.loading')}</p>}
        {selectionState === 'error' && <p role="alert">{t('inspector.error')}</p>}
        {selectedAsset && <><h2 className="inspector-title">{selectedAsset.filename}</h2><a className="primary-button full-width inspector-download" href={assetDownloadUrl(selectedAsset.id)}>{t('inspector.download')}</a><ModelViewer source={assetViewerSource(selectedAsset)} /><div className="tags">{selectedAsset.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div><div className="stats"><div className="stat"><span className="stat-label">{t('inspector.format')}</span><span className="stat-value">{selectedAsset.format.toUpperCase()}</span></div><div className="stat"><span className="stat-label">{t('inspector.path')}</span><span className="stat-value">{selectedAsset.relativePath}</span></div>{selectedAsset.byteSize !== undefined && <div className="stat"><span className="stat-label">{t('inspector.size')}</span><span className="stat-value">{t('content.fileSize', { size: byteSizeInMegabytes(selectedAsset.byteSize) })}</span></div>}</div>{threeMfCore(selectedAsset).length > 0 && <section className="asset-info"><h3>{t('metadata.title')}</h3>{threeMfCore(selectedAsset).map(([key, value]) => key === 'description' ? <div className="asset-description" key={key}><strong>{key}:</strong>{humanReadableText(value).split('\n').map((paragraph) => <p key={paragraph}>{paragraph}</p>)}</div> : <p key={key}><strong>{key}:</strong> {value}</p>)}</section>}{threeMfDocuments(selectedAsset).length > 0 && <section className="asset-info"><h3>{t('metadata.instructions')}</h3>{threeMfDocuments(selectedAsset).map((document) => <details key={document.label}><summary>{document.label}</summary>{document.text ? <pre>{humanReadableText(document.text)}</pre> : <p>{t('metadata.binaryDocument')}</p>}</details>)}</section>}<section className="asset-management">{canUpload && <><div aria-label={t('projects.assign')} className="project-picker"><span>{t('projects.assign')}</span><div className="project-assignments">{projects.map((project) => { const assigned = project.assetIds.includes(selectedAsset.id); const folderId = project.assetFolderIds[selectedAsset.id] ?? ''; return <div className="project-assignment" key={project.id}><button aria-label={`${project.name} ${t('projects.assign')}`} aria-pressed={assigned} className={`project-pill ${assigned ? 'is-assigned' : ''}`} onClick={() => void assignSelectedProject(project.id)} type="button">{project.name}</button><select aria-label={`${project.name} ${t('projects.folder')}`} disabled={!assigned} onChange={(event) => void assignSelectedProject(project.id, event.target.value || null)} value={folderId}><option value="">{t('projects.folderRoot')}</option>{project.folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></div>})}</div>{projectMessage && <p className="operation-message" role="status">{projectMessage}</p>}</div><div className="tag-management"><div className="management-heading"><h3>{t('tags.create')}</h3><button className="ghost-button" onClick={() => setTagFormOpen(true)} type="button">{t('tags.create')}</button></div>{tags.map((tag) => <label className="tag-option" key={tag.key}><input checked={selectedTagKeys.includes(tag.key)} onChange={(event) => setSelectedTagKeys((current) => event.target.checked ? [...new Set([...current, tag.key])] : current.filter((key) => key !== tag.key))} type="checkbox" />{tag.name}</label>)}{tags.length > 0 && <button className="ghost-button full-width" onClick={() => void saveSelectedTags()} type="button">{t('tags.save')}</button>}</div></>}</section>{canUpload && <section className="thumbnail-upload"><h3>{t('thumbnail.upload')}</h3><input accept="image/png,image/jpeg,image/webp" aria-label={t('thumbnail.upload')} className="visually-hidden" onChange={(event) => void uploadSelectedThumbnail(event.currentTarget.files)} ref={thumbnailInput} type="file" /><button className="ghost-button full-width" onClick={() => thumbnailInput.current?.click()} type="button">{t('thumbnail.upload')}</button></section>}{tagFormOpen && <form aria-label={t('tags.create')} className="inline-form" onSubmit={submitTag}><label>{t('tags.key')}<input onChange={(event) => setTagKey(event.target.value)} pattern="[a-z0-9][a-z0-9-]*" required value={tagKey} /></label><label>{t('tags.name')}<input onChange={(event) => setTagName(event.target.value)} required value={tagName} /></label><div className="form-actions"><button className="ghost-button" onClick={() => setTagFormOpen(false)} type="button">{t('actions.cancel')}</button><button className="primary-button" type="submit">{t('actions.save')}</button></div></form>}{canUpload && !selectedAsset.archived && <button className="ghost-button full-width" onClick={() => void archiveSelectedAsset()} type="button">{t('actions.archive')}</button>}{canUpload && selectedAsset.archived && <button className="ghost-button full-width" onClick={() => void restoreSelectedAsset()} type="button">{t('actions.restore')}</button>}{role === 'admin' && <button className="danger-button full-width" onClick={() => void deleteSelectedAsset()} type="button">{t('actions.delete')}</button>}</>}
      </aside>
    </div>
  )
}
