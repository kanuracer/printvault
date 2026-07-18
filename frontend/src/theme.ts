export const THEME_STORAGE_KEY = 'printvault.appearance'

export type ThemePreference = 'dark' | 'light' | 'system'
export type ResolvedTheme = Exclude<ThemePreference, 'system'>

const preferences = new Set<ThemePreference>(['dark', 'light', 'system'])

export function readThemePreference(storage: Storage = localStorage): ThemePreference {
  const savedPreference = storage.getItem(THEME_STORAGE_KEY)
  return preferences.has(savedPreference as ThemePreference)
    ? (savedPreference as ThemePreference)
    : 'dark'
}

export function saveThemePreference(preference: ThemePreference, storage: Storage = localStorage): void {
  storage.setItem(THEME_STORAGE_KEY, preference)
}

export function resolveTheme(preference: ThemePreference, mediaQuery?: MediaQueryList): ResolvedTheme {
  if (preference !== 'system') return preference
  return (mediaQuery ?? window.matchMedia('(prefers-color-scheme: dark)')).matches ? 'dark' : 'light'
}

export function applyTheme(preference: ThemePreference, root: HTMLElement = document.documentElement): ResolvedTheme {
  const theme = resolveTheme(preference)
  root.dataset.theme = theme
  return theme
}
