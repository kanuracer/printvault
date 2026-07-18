import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './i18n'
import { ApiError, assetDownloadUrl, getAsset, getAssets, getCurrentUser, getLibraries, uploadFiles, type Asset, type Library, type UserRole } from './api'
import { ModelViewer } from './features/viewer/ModelViewer'
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

export default function App() {
  const { t } = useTranslation()
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference)
  const [authState, setAuthState] = useState<AuthState>('loading')
  const [role, setRole] = useState<UserRole | null>(null)
  const [assetState, setAssetState] = useState<AssetState>('loading')
  const [libraries, setLibraries] = useState<Library[]>([])
  const [assets, setAssets] = useState<Asset[]>([])
  const [activeLibrary, setActiveLibrary] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null)
  const [selectionState, setSelectionState] = useState<SelectionState>('idle')
  const [uploading, setUploading] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [uploadMessage, setUploadMessage] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

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
          const [nextLibraries, nextAssets] = await Promise.all([getLibraries(), getAssets()])
          if (cancelled) return
          setLibraries(nextLibraries)
          setAssets(nextAssets)
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

  const visibleAssets = useMemo(() => {
    const term = search.trim().toLocaleLowerCase()
    return assets.filter((asset) => {
      if (activeLibrary && asset.libraryKey !== activeLibrary) return false
      if (!term) return true
      return [asset.filename, asset.relativePath, ...asset.tags].some((value) => value.toLocaleLowerCase().includes(term))
    })
  }, [activeLibrary, assets, search])

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

  const selectAsset = async (id: string) => {
    setSelectionState('loading')
    setSelectedAsset(null)
    try {
      const asset = await getAsset(id)
      setSelectedAsset(asset)
      setSelectionState('ready')
    } catch {
      setSelectionState('error')
    }
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
            <button className={`nav-item ${activeLibrary === null ? 'is-active' : ''}`} onClick={() => setActiveLibrary(null)} type="button"><span className="nav-bullet" />{t('navigation.allAssets')}</button>
            {libraries.map((library) => (
              <button className={`nav-item ${activeLibrary === library.key ? 'is-active' : ''}`} key={library.key} onClick={() => setActiveLibrary(library.key)} type="button"><span className="nav-bullet" />{library.name}</button>
            ))}
          </div>
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
          <div className="content-header"><div><p className="section-label">{t('content.eyebrow')}</p><h1>{t('content.title')}</h1>{assetState === 'ready' && <p className="result-count">{t('content.resultCount', { count: visibleAssets.length })}</p>}</div></div>
          {canUpload && uploadLibrary && <div aria-label={t('upload.dropLabel')} className={`upload-dropzone ${isDragging ? 'is-dragging' : ''}`} onClick={() => fileInput.current?.click()} onDragEnter={(event) => { event.preventDefault(); setIsDragging(true) }} onDragLeave={(event) => { event.preventDefault(); setIsDragging(false) }} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); setIsDragging(false); void handleUpload(event.dataTransfer.files) }} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); fileInput.current?.click() } }} role="button" tabIndex={0}>
            <input accept=".stl,.obj,.3mf" aria-label={t('upload.inputLabel')} className="visually-hidden" multiple onChange={(event) => void handleUpload(event.currentTarget.files ?? [])} ref={fileInput} type="file" />
            <strong>{uploading ? t('upload.uploading') : t('upload.title')}</strong><span>{t('upload.description')}</span>
          </div>}
          {uploadMessage && <p className="upload-message" role="status">{uploadMessage}</p>}
          {assetState === 'loading' && <p role="status">{t('content.loading')}</p>}
          {assetState === 'error' && <div className="content-state" role="alert"><p>{t('content.error')}</p><button className="ghost-button" onClick={loadWorkspace} type="button">{t('content.retry')}</button></div>}
          {assetState === 'ready' && visibleAssets.length === 0 && <div className="content-state"><h2>{t('content.emptyTitle')}</h2><p>{t('content.emptyDescription')}</p></div>}
          {assetState === 'ready' && visibleAssets.length > 0 && <div aria-busy="false" className="asset-grid">{visibleAssets.map((asset) => <article className="asset-card" key={asset.id}><button aria-label={asset.filename} className="asset-card-button" onClick={() => void selectAsset(asset.id)} type="button"><div className="asset-preview"><div aria-hidden="true" className="model-shape" /></div><div className="asset-body"><h2 className="asset-name">{asset.filename}</h2><p className="asset-meta">{asset.byteSize === undefined ? t('content.assetMeta', { format: asset.format.toUpperCase(), path: asset.relativePath }) : t('content.assetMetaWithSize', { format: asset.format.toUpperCase(), path: asset.relativePath, size: t('content.fileSize', { size: byteSizeInMegabytes(asset.byteSize) }) })}</p><div className="tags">{asset.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div></div></button></article>)}</div>}
        </section>
      </main>

      <aside aria-label={t('inspector.label')} className="inspector" role="complementary">
        <div className="inspector-header"><span className="section-label">{t('inspector.label')}</span></div>
        {selectionState === 'idle' && <div className="content-state"><h2 className="inspector-title">{t('inspector.emptyTitle')}</h2><p>{t('inspector.emptyDescription')}</p></div>}
        {selectionState === 'loading' && <p role="status">{t('inspector.loading')}</p>}
        {selectionState === 'error' && <p role="alert">{t('inspector.error')}</p>}
        {selectedAsset && <><ModelViewer source={assetViewerSource(selectedAsset)} /><h2 className="inspector-title">{selectedAsset.filename}</h2><div className="tags">{selectedAsset.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div><div className="stats"><div className="stat"><span className="stat-label">{t('inspector.format')}</span><span className="stat-value">{selectedAsset.format.toUpperCase()}</span></div><div className="stat"><span className="stat-label">{t('inspector.path')}</span><span className="stat-value">{selectedAsset.relativePath}</span></div>{selectedAsset.byteSize !== undefined && <div className="stat"><span className="stat-label">{t('inspector.size')}</span><span className="stat-value">{t('content.fileSize', { size: byteSizeInMegabytes(selectedAsset.byteSize) })}</span></div>}</div><a className="primary-button full-width" href={assetDownloadUrl(selectedAsset.id)}>{t('inspector.download')}</a></>}
      </aside>
    </div>
  )
}
