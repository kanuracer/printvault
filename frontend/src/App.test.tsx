import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { THEME_STORAGE_KEY } from './theme'

vi.mock('./features/viewer/ModelViewer', () => ({
  ModelViewer: ({ source }: { source: unknown }) => <output data-testid="model-viewer">{JSON.stringify(source)}</output>,
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

  it('renders the localized empty state for an authenticated library with no assets', async () => {
    vi.mocked(fetch).mockImplementation(authenticatedResponses([]))

    render(<App />)

    expect(await screen.findByText('Noch keine Modelle')).toBeVisible()
    expect(screen.getByText('Neue Modelle erscheinen hier, sobald sie verfügbar sind.')).toBeVisible()
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
})
