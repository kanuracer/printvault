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

const authenticatedResponses = (assets: unknown, detail = assets, role: 'viewer' | 'editor' | 'admin' = 'viewer') => (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role }))
  if (url === '/api/preferences/appearance' && init?.method === 'PUT') return Promise.resolve(jsonResponse(JSON.parse(init.body as string)))
  if (url === '/api/preferences/appearance') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
  if (url === '/api/preferences/explorer' && init?.method === 'PUT') return Promise.resolve(jsonResponse(JSON.parse(init.body as string)))
  if (url === '/api/preferences/explorer') return Promise.resolve(jsonResponse({ view: 'grid', page_size: 50 }))
  if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
  if (url === '/api/helper/devices') return Promise.resolve(jsonResponse({ items: [] }))
  if (url === '/api/helper/pairing-codes' && init?.method === 'POST') return Promise.resolve(jsonResponse({ pairing_code: 'PAIR-000001', expires_at: '2026-07-20T12:05:00Z' }, 201))
  if (url === '/api/admin/libraries/models/exclude-rules' && init?.method === 'POST') return Promise.resolve(jsonResponse({ items: [{ pattern: 'drafts/**/*.stl' }] }, 201))
  if (url === '/api/admin/libraries/models/exclude-rules' && init?.method === 'DELETE') return Promise.resolve(jsonResponse({ items: [] }))
  if (url === '/api/admin/libraries/models/exclude-rules') return Promise.resolve(jsonResponse({ items: [] }))
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

  it('loads and saves the appearance preference through the authenticated API', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role: 'viewer' }))
      if (url === '/api/preferences/appearance' && !init?.method) return Promise.resolve(jsonResponse({ appearance: 'light' }))
      if (url === '/api/preferences/appearance' && init?.method === 'PUT') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)

    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('light'))
    await user.click(screen.getByRole('button', { name: 'Einstellungen' }))
    await user.click(screen.getByLabelText('Dunkel'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/preferences/appearance', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ appearance: 'dark' }),
    })))
    expect(document.documentElement.dataset.theme).toBe('dark')
    await user.click(screen.getByRole('button', { name: 'Einstellungen' }))
    expect(screen.queryByLabelText('Dunkel')).not.toBeInTheDocument()
  })

  it('loads the server explorer preference and persists a list view change', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'user-1', role: 'viewer' }))
      if (url === '/api/preferences/appearance') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
      if (url === '/api/preferences/explorer' && init?.method === 'PUT') return Promise.resolve(jsonResponse(JSON.parse(init.body as string)))
      if (url === '/api/preferences/explorer') return Promise.resolve(jsonResponse({ view: 'grid', page_size: 50 }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Listenansicht' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/preferences/explorer', expect.objectContaining({
      method: 'PUT', body: JSON.stringify({ view: 'list', page_size: 50 }),
    })))
  })

  it('selects multiple assets and assigns a tag through the atomic batch API', async () => {
    const fetchMock = vi.mocked(fetch)
    const assets = [
      { id: 'asset-1', library_key: 'models', relative_path: 'Bracket.stl', filename: 'Bracket.stl', format: 'stl', tags: [] },
      { id: 'asset-2', library_key: 'models', relative_path: 'Cube.obj', filename: 'Cube.obj', format: 'obj', tags: [] },
    ]
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/preferences/appearance') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
      if (url === '/api/preferences/explorer') return Promise.resolve(jsonResponse({ view: 'grid', page_size: 50 }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: assets, total: assets.length }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [{ key: 'art', name: 'Art' }] }))
      if (url === '/api/assets/batch/tags' && init?.method === 'POST') return Promise.resolve(jsonResponse({ items: assets.map((asset) => ({ ...asset, tags: ['art'] })) }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)

    await user.click(await screen.findByRole('checkbox', { name: 'Modell auswählen: Bracket.stl' }))
    await user.click(screen.getByRole('checkbox', { name: 'Modell auswählen: Cube.obj' }))
    await user.selectOptions(screen.getByLabelText('Tag für Auswahl'), 'art')
    await user.click(screen.getByRole('button', { name: 'Tag zuweisen' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/assets/batch/tags', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ asset_ids: ['asset-1', 'asset-2'], tag_keys: ['art'] }),
    })))
    expect(screen.getAllByText('art')).toHaveLength(2)
  })

  it('confirms before batch archive and clears selection only after success', async () => {
    const fetchMock = vi.mocked(fetch)
    const assets = [
      { id: 'asset-1', library_key: 'models', relative_path: 'Bracket.stl', filename: 'Bracket.stl', format: 'stl', tags: [], archived: false },
      { id: 'asset-2', library_key: 'models', relative_path: 'Cube.obj', filename: 'Cube.obj', format: 'obj', tags: [], archived: false },
    ]
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/preferences/appearance') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
      if (url === '/api/preferences/explorer') return Promise.resolve(jsonResponse({ view: 'grid', page_size: 50 }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: assets, total: assets.length }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets/batch/archive' && init?.method === 'POST') return Promise.resolve(jsonResponse({ items: assets.map((asset) => ({ ...asset, library_key: 'archive', relative_path: `models/${asset.relative_path}`, archived: true })) }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<App />)

    await user.click(await screen.findByRole('checkbox', { name: 'Modell auswählen: Bracket.stl' }))
    await user.click(screen.getByRole('checkbox', { name: 'Modell auswählen: Cube.obj' }))
    await user.click(screen.getByRole('button', { name: 'Auswahl archivieren' }))

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledWith('2 ausgewählte Modelle ins Archiv verschieben?'))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/assets/batch/archive', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ asset_ids: ['asset-1', 'asset-2'] }),
    })))
    expect(screen.getByText('2 Modelle archiviert.')).toBeVisible()
    expect(screen.queryByRole('checkbox', { name: 'Modell auswählen: Bracket.stl', checked: true })).not.toBeInTheDocument()
    expect(screen.queryByText('Bracket.stl')).not.toBeInTheDocument()
  })

  it('keeps the batch selection when batch archive fails', async () => {
    const fetchMock = vi.mocked(fetch)
    const assets = [
      { id: 'asset-1', library_key: 'models', relative_path: 'Bracket.stl', filename: 'Bracket.stl', format: 'stl', tags: [], archived: false },
      { id: 'asset-2', library_key: 'models', relative_path: 'Cube.obj', filename: 'Cube.obj', format: 'obj', tags: [], archived: false },
    ]
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/preferences/appearance') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
      if (url === '/api/preferences/explorer') return Promise.resolve(jsonResponse({ view: 'grid', page_size: 50 }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: assets, total: assets.length }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets/batch/archive' && init?.method === 'POST') return Promise.resolve(jsonResponse({ detail: 'batch archive failed' }, 500))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    render(<App />)

    await user.click(await screen.findByRole('checkbox', { name: 'Modell auswählen: Bracket.stl' }))
    await user.click(screen.getByRole('checkbox', { name: 'Modell auswählen: Cube.obj' }))
    await user.click(screen.getByRole('button', { name: 'Auswahl archivieren' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('batch archive failed')
    expect(screen.getByRole('checkbox', { name: 'Modell auswählen: Bracket.stl' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Modell auswählen: Cube.obj' })).toBeChecked()
    expect(screen.getByText('Bracket.stl')).toBeVisible()
  })

  it('does not expose the legacy filesystem projects library as a model library', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'viewer-1', role: 'viewer' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Models' }, { key: 'projects', name: 'Projects' }, { key: 'archive', name: 'Archive' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })

    render(<App />)
    const libraries = await screen.findByRole('navigation', { name: 'Bibliotheken' })
    expect(within(libraries).queryByRole('button', { name: 'Projects' })).not.toBeInTheDocument()
  })

  it('shows the admin exclude-rule panel only for admins and surfaces validation errors', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'admin-1', role: 'admin' }))
      if (url === '/api/preferences/appearance') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
      if (url === '/api/preferences/explorer') return Promise.resolve(jsonResponse({ view: 'grid', page_size: 50 }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/admin/libraries/models/exclude-rules' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        return Promise.resolve(body.pattern === '../escape'
          ? jsonResponse({ detail: 'exclude pattern must be a non-escaping relative glob' }, 422)
          : jsonResponse({ items: [{ pattern: 'drafts/**/*.stl' }] }, 201))
      }
      if (url === '/api/admin/libraries/models/exclude-rules') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [], total: 0 }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Einstellungen' }))
    expect(await screen.findByText('Bibliothek-Ausschlussregeln')).toBeVisible()
    await user.type(screen.getByLabelText('Muster'), '../escape')
    await user.click(screen.getByRole('button', { name: 'Muster hinzufügen' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('exclude pattern must be a non-escaping relative glob')
    await user.clear(screen.getByLabelText('Muster'))
    await user.type(screen.getByLabelText('Muster'), 'drafts/**/*.stl')
    await user.click(screen.getByRole('button', { name: 'Muster hinzufügen' }))
    expect(await screen.findByText('Ausschlussregel gespeichert.')).toBeVisible()
    expect(screen.getByText('drafts/**/*.stl')).toBeVisible()
  })

  it('renders helper pairing and owned-device management without exposing credentials', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'viewer-1', role: 'viewer' }))
      if (url === '/api/preferences/appearance') return Promise.resolve(jsonResponse({ appearance: 'dark' }))
      if (url === '/api/preferences/explorer') return Promise.resolve(jsonResponse({ view: 'grid', page_size: 50 }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/helper/devices/device-1' && init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }))
      if (url === '/api/helper/devices') return Promise.resolve(jsonResponse({ items: [{ device_id: 'device-1', name: 'Werkbank', created_at: '2026-07-20T12:00:00Z' }] }))
      if (url === '/api/helper/pairing-codes' && init?.method === 'POST') return Promise.resolve(jsonResponse({ pairing_code: 'PAIR-123456', expires_at: '2026-07-20T12:05:00Z' }, 201))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [], total: 0 }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Einstellungen' }))
    expect(await screen.findByText('Helper')).toBeVisible()
    expect(screen.getByText('Werkbank')).toBeVisible()
    expect(screen.queryByText(/device_credential/i)).not.toBeInTheDocument()
    expect(screen.getByText(/Der Helper speichert die Geräteanmeldung lokal/)).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Kopplungscode erzeugen' }))
    expect(await screen.findByText('PAIR-123456')).toBeVisible()
    expect(screen.getByText(/Gültig bis:/)).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Entziehen' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/helper/devices/device-1', expect.objectContaining({ method: 'DELETE' })))
    await waitFor(() => expect(screen.queryByText('Werkbank')).not.toBeInTheDocument())
    expect(screen.getByText('Gerät entzogen.')).toBeVisible()
  })

  it('opens mobile navigation and details as dismissible dialogs', async () => {
    vi.mocked(fetch).mockImplementation(authenticatedResponses([]))
    const user = userEvent.setup()
    render(<App />)

    await user.click(await screen.findByRole('button', { name: 'Navigation öffnen' }))
    const navigationDialog = await screen.findByRole('dialog', { name: 'Bibliotheken' })
    await user.click(within(navigationDialog).getByRole('button', { name: 'Einstellungen' }))
    expect(await within(navigationDialog).findByRole('region', { name: 'Helper' })).toBeVisible()
    expect(navigationDialog).toBeVisible()
    expect(within(navigationDialog).getByRole('link', { name: 'Helper für Windows herunterladen' })).toHaveAttribute('href', 'https://github.com/kanuracer/printvault/releases/latest/download/printvault-helper-windows.zip')
    await user.click(within(navigationDialog).getByRole('button', { name: 'Schließen' }))
    expect(screen.queryByRole('dialog', { name: 'Bibliotheken' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Details öffnen' }))
    expect(await screen.findByRole('dialog', { name: 'Details' })).toBeVisible()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Details' })).not.toBeInTheDocument()
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
    const libraryWorkbench = await waitFor(() => {
      const element = document.querySelector('.library-workbench')
      expect(element).toBeInTheDocument()
      return element as HTMLElement
    })
    expect(libraryWorkbench).toContainElement(screen.getByRole('region', { name: 'Projektfilter' }))
    expect(libraryWorkbench.querySelector('.upload-dropzone')).toBeInTheDocument()
    const files = [new File(['solid bracket'], 'bracket.stl', { type: 'model/stl' }), new File(['o case'], 'case.obj', { type: 'model/obj' })]
    await user.upload(await screen.findByLabelText('Modelle hochladen'), files)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/uploads', expect.objectContaining({ method: 'POST' })))
  })

  it('asks whether a duplicate upload should overwrite or receive an adjusted name', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [], total: 0 }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/uploads') {
        const policy = (init?.body as FormData).get('collision_policy')
        return Promise.resolve(policy === 'overwrite'
          ? jsonResponse({ items: [{ id: 'asset-1', library_key: 'models', relative_path: 'bracket.stl', filename: 'bracket.stl', format: 'stl', favorite: false, tags: [], archived: false }], rejected: [] })
          : jsonResponse({ items: [], rejected: [{ filename: 'bracket.stl', reason: 'collision' }] }))
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)
    await user.upload(await screen.findByLabelText('Modelle hochladen'), new File(['solid replacement'], 'bracket.stl', { type: 'model/stl' }))

    const dialog = await screen.findByRole('dialog', { name: 'Duplikat erkannt' })
    expect(within(dialog).getByText(/bracket\.stl/)).toBeVisible()
    await user.click(within(dialog).getByRole('button', { name: 'Bestehendes Modell überschreiben' }))
    await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith('/api/uploads', expect.objectContaining({ method: 'POST' })))
    const body = vi.mocked(fetch).mock.calls.at(-1)?.[1]?.body as FormData
    expect(body.get('collision_policy')).toBe('overwrite')
    expect(screen.queryByRole('dialog', { name: 'Duplikat erkannt' })).not.toBeInTheDocument()
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
      if (url === '/api/assets?project_id=project-1') return Promise.resolve(jsonResponse({ items: [asset] }))
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
      if (url === '/api/assets?project_id=project-1') return Promise.resolve(jsonResponse({ items: [rootAsset] }))
      if (url === '/api/assets?project_id=project-1&folder_id=folder-1') return Promise.resolve(jsonResponse({ items: [folderAsset] }))
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
    expect(await screen.findByRole('button', { name: /Root\.stl/i })).toBeVisible()
    expect(screen.queryByRole('button', { name: /Folder\.stl/i })).not.toBeInTheDocument()
  })

  it('filters all models by project and tag toggles', async () => {
    const alpha = { id: 'alpha', library_key: 'models', relative_path: 'alpha.stl', filename: 'Alpha.stl', format: 'stl', tags: ['functional'] }
    const beta = { id: 'beta', library_key: 'models', relative_path: 'beta.stl', filename: 'Beta.stl', format: 'stl', tags: ['decorative'] }
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'viewer-1', role: 'viewer' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [alpha, beta] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [{ id: 'project-1', name: 'Werkbank', description: '', asset_ids: ['alpha'], folders: [], asset_folder_ids: {} }] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [{ key: 'functional', name: 'Funktional' }, { key: 'decorative', name: 'Dekorativ' }] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)
    expect(await screen.findByRole('button', { name: 'Alpha.stl' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Projektfilter' }))
    await user.click(screen.getByRole('option', { name: 'Werkbank' }))
    expect(screen.getByRole('button', { name: 'Alpha.stl' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Beta.stl' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tagfilter' }))
    await user.click(screen.getByRole('option', { name: 'Funktional' }))
    expect(screen.getByRole('button', { name: 'Alpha.stl' })).toBeVisible()
  })


  it('limits project assignment results to 30 and searches 10,000 projects', async () => {
    const asset = { id: 'asset-1', library_key: 'models', relative_path: 'widget.stl', filename: 'Widget.stl', format: 'stl', tags: [] }
    const projects = Array.from({ length: 10_000 }, (_, index) => ({ id: `project-${index}`, name: `Projekt ${index}`, description: '', asset_ids: [], folders: [], asset_folder_ids: {} }))
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [asset] }))
      if (url === '/api/assets/asset-1') return Promise.resolve(jsonResponse(asset))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: projects }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()

    render(<App />)
    await user.click(await screen.findByRole('button', { name: 'Widget.stl' }))
    await user.click(await screen.findByRole('button', { name: 'Projekt zuweisen' }))
    expect(await screen.findAllByRole('option')).toHaveLength(30)
    expect(screen.queryByRole('option', { name: 'Projekt 9999' })).not.toBeInTheDocument()
    await user.type(screen.getByRole('searchbox', { name: 'Projekte durchsuchen' }), 'Projekt 9999')
    expect(await screen.findByRole('option', { name: 'Projekt 9999' })).toBeVisible()
  })

  it('moves a project model into a child folder by dropping its card on that folder', async () => {
    const asset = { id: 'asset-1', library_key: 'models', relative_path: 'Widget.stl', filename: 'Widget.stl', format: 'stl', tags: [] }
    const project = {
      id: 'project-1', name: 'Werkbank', description: '', asset_ids: ['asset-1'],
      folders: [{ id: 'folder-1', name: 'Druckteile', parent_id: null }], asset_folder_ids: {},
    }
    const movedProject = { ...project, asset_folder_ids: { 'asset-1': 'folder-1' } }
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/auth/me') return Promise.resolve(jsonResponse({ subject: 'editor-1', role: 'editor' }))
      if (url === '/api/libraries') return Promise.resolve(jsonResponse({ items: [{ key: 'models', name: 'Modelle' }] }))
      if (url === '/api/assets') return Promise.resolve(jsonResponse({ items: [asset] }))
      if (url === '/api/assets?project_id=project-1') return Promise.resolve(jsonResponse({ items: [asset] }))
      if (url === '/api/projects') return Promise.resolve(jsonResponse({ items: [project] }))
      if (url === '/api/tags') return Promise.resolve(jsonResponse({ items: [] }))
      if (url === '/api/projects/project-1/assets/asset-1' && init?.method === 'PUT') return Promise.resolve(jsonResponse(movedProject))
      return Promise.reject(new Error(`Unexpected request: ${url}`))
    })
    const user = userEvent.setup()
    const dataTransfer = { effectAllowed: '', getData: vi.fn(() => 'asset-1'), setData: vi.fn() } as unknown as DataTransfer

    render(<App />)
    await user.click(await screen.findByRole('button', { name: /Werkbank/ }))
    const modelCard = screen.getByRole('button', { name: 'Widget.stl' }).closest('article')
    const folderCard = screen.getByRole('button', { name: 'Druckteile' })
    expect(modelCard).not.toBeNull()

    fireEvent.dragStart(modelCard!, { dataTransfer })
    fireEvent.dragOver(folderCard, { dataTransfer })
    fireEvent.drop(folderCard, { dataTransfer })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects/project-1/assets/asset-1',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ folder_id: 'folder-1' }) }),
    ))
    expect(await screen.findByText('„Widget.stl“ wurde nach „Druckteile“ verschoben.')).toBeVisible()
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
    await user.click(screen.getByRole('button', { name: 'Einstellungen' }))
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
      if (url === '/api/projects/project-1/assets/asset-1' && init?.method === 'DELETE') return Promise.resolve(jsonResponse({ id: 'project-1', name: 'Drucker', description: '', asset_ids: [] }))
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
    await user.click(await screen.findByRole('button', { name: 'Projekt hinzufügen' }))
    const projectDialog = await screen.findByRole('dialog', { name: 'Projekt erstellen' })
    await user.type(within(projectDialog).getByLabelText('Projektname'), 'Drucker')
    await user.click(within(projectDialog).getByRole('button', { name: 'Speichern' }))
    expect(await screen.findByRole('navigation', { name: 'Projekte' })).toBeVisible()
    expect(screen.getByRole('button', { name: /^Drucker\b/ })).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Widget\.stl/i }))
    await user.click(await screen.findByRole('button', { name: 'Tag erstellen' }))
    await user.type(screen.getByLabelText('Tag-Schlüssel'), 'functional')
    await user.type(screen.getByLabelText('Tag-Name'), 'Funktional')
    await user.click(screen.getByRole('button', { name: 'Speichern' }))
    await user.click(screen.getByLabelText('Funktional'))
    await user.click(screen.getByRole('button', { name: 'Tags speichern' }))
    await user.click(screen.getByRole('button', { name: 'Projekt zuweisen' }))
    await user.click(await screen.findByRole('option', { name: 'Drucker' }))
    await user.click(await screen.findByRole('button', { name: 'Drucker Aus Projekt entfernen' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/projects/project-1/assets/asset-1', expect.objectContaining({ method: 'DELETE' })))
    await user.click(screen.getByRole('button', { name: 'Archivieren' }))
    await user.click(screen.getByRole('button', { name: 'Archiv' }))
    await user.click(await screen.findByRole('button', { name: /Widget\.stl/i }))
    await user.click(screen.getByRole('button', { name: 'Wiederherstellen' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/assets/asset-1/restore', expect.objectContaining({ method: 'POST' })))
  })
})
