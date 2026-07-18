import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { THEME_STORAGE_KEY } from './theme'

vi.mock('./features/viewer/ModelViewer', () => ({
  ModelViewer: ({ source }: { source: unknown }) => <output data-testid="model-viewer">{JSON.stringify(source)}</output>,
}))

vi.mock('./features/viewer/AssetThumbnail', () => ({
  AssetThumbnail: ({ assetId }: { assetId: string }) => <output data-testid="asset-thumbnail">{assetId}</output>,
}))

const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json' },
})

const authenticatedResponses = (assets: unknown, detail = assets, role: 'viewer' | 'editor' | 'admin' = 'viewer') => (input: RequestInfo | URL) => {
  const url = String(input)
  if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role }))
  if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
  if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: assets, total: Array.isArray(assets) ? assets.length : 0 }))
  if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
  if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
  if (url === '/api/assets/asset%20id%2F1') return Promise.resolve(jsonResponse(detail))
  return Promise.reject(new Error(`Unexpected request: ${url}`))
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  vi.unstubAllGlobals()
})

describe('PrintVault authenticated asset library', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('shows the localized sign-in action when the BFF session is absent', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'authentication is required' }, 401))

    render(<App />)

    const signIn = await screen.findByRole('link', { name: 'Anmelden' })
    expect(signIn).toHaveAttribute('href', '/api/auth/login')
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/me', expect.objectContaining({ credentials: 'same-origin' }))
  })

  it('shows localized access denial with a fresh sign-in action for a forbidden BFF session', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: 'PrintVault access is not granted' }, 403))

    render(<App />)

    expect(await screen.findByText('Du hast keinen Zugriff auf PrintVault.')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Erneut anmelden' })).toHaveAttribute('href', '/api/auth/login')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('uploads multiple supported files with the editor upload control', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [], total: 0 }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/uploads') {
        const body = init?.body
        expect(body).toBeInstanceOf(FormData)
        expect((body as FormData).get('library_key')).toBe('models')
        expect((body as FormData).getAll('files').map((file) => (file as File).name)).toEqual(['bracket.stl', 'case.obj'])
        return Promise.resolve(jsonResponse({ items: [], rejected: [] }))
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)
    const files = [new File(['solid bracket'], 'bracket.stl', { type: 'model/stl' }), new File(['o case'], 'case.obj', { type: 'model/obj' })]
    await user.upload(await screen.findByLabelText('Modelle hochladen'), files)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/uploads', expect.objectContaining({ method: 'POST' })))
  })

  it('accepts dropped files in the editor drop zone', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [], total: 0 }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/uploads') return Promise.resolve(jsonResponse({ items: [], rejected: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })

    render(<App />)
    fireEvent.drop(await screen.findByLabelText('Dateien hier ablegen'), {
      dataTransfer: { files: [new File(['solid dropped'], 'dropped.stl', { type: 'model/stl' })] },
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/uploads', expect.objectContaining({ method: 'POST' })))
  })

  it('renders real API metadata and opens a viewer from the selected API asset record', async () => {
    const asset = {
      id: 'asset id/1',
      library_key: 'models',
      relative_path: 'functional/Widget.stl',
      filename: 'Widget.stl',
      format: 'stl',
      tags: ['functional', 'printer'],
      favorite: false,
      archived: false,
    }
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation(authenticatedResponses([asset], asset))
    const user = userEvent.setup()

    render(<App />)

    expect(await screen.findByRole('button', { name: /Widget\.stl/i })).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith('/api/libraries', expect.objectContaining({ credentials: 'same-origin' }))
    expect(fetchMock).toHaveBeenCalledWith('/api/assets', expect.objectContaining({ credentials: 'same-origin' }))
    expect(screen.getByText('functional')).toBeVisible()
    expect(screen.getByTestId('asset-thumbnail')).toHaveTextContent('asset id/1')
    await user.click(screen.getByRole('button', { name: /Widget\.stl/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/assets/asset%20id%2F1',
      expect.objectContaining({ credentials: 'same-origin' }),
    ))
    expect(await screen.findByTestId('model-viewer')).toHaveTextContent(JSON.stringify({
      kind: 'authenticated-api',
      url: '/api/assets/asset%20id%2F1/download',
      format: 'stl',
    }))
    expect(screen.getByRole('link', { name: 'Herunterladen' })).toHaveAttribute(
      'href',
      '/api/assets/asset%20id%2F1/download',
    )
  })

  it('puts download directly below the title and renders escaped descriptions as readable text', async () => {
    const asset = {
      id: 'asset id/1', library_key: 'models', relative_path: 'Topper.3mf', filename: 'Topper.3mf', format: '3mf', tags: [], favorite: false, archived: false,
      metadata: { three_mf: { core: { description: '&lt;p&gt;Ready to print “oh baby topper” &lt;/p&gt;&lt;p&gt;Happy printing.&lt;/p&gt;' } } },
    }
    vi.mocked(fetch).mockImplementation(authenticatedResponses([asset], asset))
    const user = userEvent.setup()

    render(<App />)
    await user.click(await screen.findByRole('button', { name: /Topper\.3mf/i }))

    const inspector = screen.getByRole('complementary', { name: 'Details' })
    const title = await within(inspector).findByRole('heading', { name: 'Topper.3mf' })
    const download = within(inspector).getByRole('link', { name: 'Herunterladen' })
    expect(title.compareDocumentPosition(download) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('Ready to print “oh baby topper”')).toBeVisible()
    expect(screen.getByText('Happy printing.')).toBeVisible()
    expect(screen.queryByText(/&lt;p&gt;/)).not.toBeInTheDocument()
  })


  it('lets an editor upload a manual image thumbnail for a selected model', async () => {
    const asset = { id: 'asset-1', library_key: 'models', relative_path: 'Widget.stl', filename: 'Widget.stl', format: 'stl', tags: [], favorite: false, archived: false }
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [asset] }))
      if (url === '/api/projects' || url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets/asset-1') return Promise.resolve(jsonResponse(asset))
      if (url === '/api/assets/asset-1/thumbnail' && init?.method === 'POST') return Promise.resolve(jsonResponse(asset))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)
    await user.click(await screen.findByRole('button', { name: /Widget\.stl/i }))
    await user.upload(await screen.findByLabelText('Eigenes Vorschaubild'), new File(['\u0089PNG\r\n\u001a\nimage'], 'thumb.png', { type: 'image/png' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/assets/asset-1/thumbnail', expect.objectContaining({ method: 'POST' })))
  })

  it('renders the localized empty state for an authenticated library with no assets', async () => {
    vi.mocked(fetch).mockImplementation(authenticatedResponses([]))

    render(<App />)

    expect(await screen.findByText('Noch keine Modelle')).toBeVisible()
    expect(screen.getByText('Neue Modelle erscheinen hier, sobald sie verfügbar sind.')).toBeVisible()
  })

  it('shows project models after a library filter was previously selected', async () => {
    const asset = { id: 'asset-1', library_key: 'models', relative_path: 'Widget.stl', filename: 'Widget.stl', format: 'stl', tags: [], favorite: false, archived: false }
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role: 'viewer' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }, { key: 'archive', name: 'Archiv' }] }))
      if (url === '/api/assets?library=archive') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [asset] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [{ id: 'project-1', name: 'Drucker', description: '', asset_ids: ['asset-1'] }] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Archiv' }))
    expect(await screen.findByText('Noch keine Modelle')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /^Drucker\b/ }))

    expect(await screen.findByRole('button', { name: /Widget\.stl/i })).toBeVisible()
  })

  it('restores a project folder after refresh and excludes its root models', async () => {
    const rootAsset = { id: 'root-asset', library_key: 'models', relative_path: 'Root.stl', filename: 'Root.stl', format: 'stl', tags: [], favorite: false, archived: false }
    const folderAsset = { id: 'folder-asset', library_key: 'models', relative_path: 'Folder.stl', filename: 'Folder.stl', format: 'stl', tags: [], favorite: false, archived: false }
    localStorage.setItem('printvault.explorer-location', JSON.stringify({ projectId: 'project-1', folderId: 'folder-1' }))
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role: 'viewer' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [rootAsset, folderAsset] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [{ id: 'project-1', name: 'Drucker', description: '', asset_ids: ['root-asset', 'folder-asset'], folders: [{ id: 'folder-1', name: 'Counter', parent_id: null }], asset_folder_ids: { 'folder-asset': 'folder-1' } }] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Counter' })).toBeVisible()
    expect(screen.getByRole('button', { name: /Folder\.stl/i })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Root\.stl/i })).not.toBeInTheDocument()
  })

  it('restores the project root when browser history goes back from a folder', async () => {
    const rootAsset = { id: 'root-asset', library_key: 'models', relative_path: 'Root.stl', filename: 'Root.stl', format: 'stl', tags: [], favorite: false, archived: false }
    const folderAsset = { id: 'folder-asset', library_key: 'models', relative_path: 'Folder.stl', filename: 'Folder.stl', format: 'stl', tags: [], favorite: false, archived: false }
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role: 'viewer' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [rootAsset, folderAsset] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [{ id: 'project-1', name: 'Drucker', description: '', asset_ids: ['root-asset', 'folder-asset'], folders: [{ id: 'folder-1', name: 'Counter', parent_id: null }], asset_folder_ids: { 'folder-asset': 'folder-1' } }] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)
    await user.click(await screen.findByRole('button', { name: /^Drucker\b/ }))
    await user.click(await screen.findByRole('button', { name: 'Counter' }))
    expect(await screen.findByRole('heading', { name: 'Counter' })).toBeVisible()

    fireEvent.popState(window, { state: { printvaultExplorer: true, location: { libraryKey: null, projectId: 'project-1', folderId: null, showProjects: false } } })

    expect(await screen.findByRole('heading', { name: 'Drucker' })).toBeVisible()
    expect(screen.getByRole('button', { name: /Root\.stl/i })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Folder\.stl/i })).not.toBeInTheDocument()
  })

  it('renders the localized asset loading failure without demo content', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role: 'viewer' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ detail: 'failure' }, 500))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })

    render(<App />)

    expect(await screen.findByText('Modelle konnten nicht geladen werden.')).toBeVisible()
    expect(screen.queryByText('Düsengehäuse')).not.toBeInTheDocument()
  })

  it('lets an authenticated user choose light appearance and saves it', async () => {
    vi.mocked(fetch).mockImplementation(authenticatedResponses([]))
    const user = userEvent.setup()
    render(<App />)

    await screen.findByText('Noch keine Modelle')
    await user.click(screen.getByLabelText('Hell'))

    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('lets an editor create projects and tags, assign both, then restore an archived asset', async () => {
    const asset = { id: 'asset-1', library_key: 'models', relative_path: 'Widget.stl', filename: 'Widget.stl', format: 'stl', tags: [], favorite: false, archived: false }
    const archived = { ...asset, library_key: 'archive', archived: true }
    const restored = { ...asset }
    let archiveMode = false
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }, { key: 'archive', name: 'Archiv' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [asset] }))
      if (url === '/api/projects' && init?.method === 'POST') return Promise.resolve(jsonResponse({ id: 'project-1', name: 'Drucker', description: '', asset_ids: [] }, 201))
      if (url === '/api/tags' && init?.method === 'POST') return Promise.resolve(jsonResponse({ key: 'functional', name: 'Funktional' }, 201))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets/asset-1') return Promise.resolve(jsonResponse(archiveMode ? archived : asset))
      if (url === '/api/projects/project-1/assets/asset-1' && init?.method === 'PUT') return Promise.resolve(jsonResponse({ id: 'project-1', name: 'Drucker', description: '', asset_ids: ['asset-1'] }))
      if (url === '/api/assets/asset-1/tags' && init?.method === 'PUT') return Promise.resolve(jsonResponse({ ...asset, tags: ['functional'] }))
      if (url === '/api/assets/asset-1/archive') {
        archiveMode = true
        return Promise.resolve(jsonResponse(archived))
      }
      if (url === '/api/assets?library=archive') return Promise.resolve(jsonResponse({ items: [archived] }))
      if (url === '/api/assets/asset-1/restore') return Promise.resolve(jsonResponse(restored))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Projekt erstellen' }))
    await user.type(screen.getByLabelText('Projektname'), 'Drucker')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))
    expect(await screen.findByRole('navigation', { name: 'Projekte' })).toBeVisible()
    expect(screen.getByRole('button', { name: /^Drucker\b/ })).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Widget\.stl/i }))
    await user.click(await screen.findByRole('button', { name: 'Tag erstellen' }))
    await user.type(screen.getByLabelText('Tag-Schlüssel'), 'functional')
    await user.type(screen.getByLabelText('Tag-Name'), 'Funktional')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))
    await user.click(screen.getByLabelText('Funktional'))
    await user.click(screen.getByRole('button', { name: 'Tags speichern' }))
    await user.click(screen.getByRole('button', { name: 'Drucker Projekt zuweisen' }))
    await user.click(screen.getByRole('button', { name: 'Archivieren' }))
    await user.click(screen.getByRole('button', { name: 'Archiv' }))
    await user.click(await screen.findByRole('button', { name: /Widget\.stl/i }))
    await user.click(screen.getByRole('button', { name: 'Wiederherstellen' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/assets/asset-1/restore', expect.objectContaining({ method: 'POST' })))
  })
})
