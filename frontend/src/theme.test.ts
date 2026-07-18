import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  THEME_STORAGE_KEY,
  applyTheme,
  readThemePreference,
  resolveTheme,
  saveThemePreference,
  type ThemePreference,
} from './theme'

const originalMatchMedia = window.matchMedia

afterEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  window.matchMedia = originalMatchMedia
})

describe('theme preferences', () => {
  it('defaults to dark and persists a user selection under the stable key', () => {
    expect(readThemePreference()).toBe('dark')

    saveThemePreference('light')

    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(readThemePreference()).toBe('light')
  })

  it('uses the operating system when system appearance is selected', () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as typeof window.matchMedia

    expect(resolveTheme('system')).toBe('dark')

    window.matchMedia = vi.fn().mockReturnValue({ matches: false }) as typeof window.matchMedia
    expect(resolveTheme('system')).toBe('light')
  })

  it.each<ThemePreference>(['dark', 'light', 'system'])('applies %s to the document', (preference) => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: false }) as typeof window.matchMedia

    applyTheme(preference)

    expect(document.documentElement.dataset.theme).toBe(preference === 'system' ? 'light' : preference)
  })
})
