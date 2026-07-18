import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const sourceRoot = join(process.cwd(), 'src')
const germanDisplayPattern = /[ÄÖÜäöüß]|\b(?:Abbrechen|Archiv|Bibliotheken|Details|Drucken|Einstellungen|Hell|Leer|Modelle|Neu|Suchen|System|Thema|Version)\b/i
const stringLiteralPattern = /(['"`])((?:\\[\s\S]|(?!\1)[\s\S])*?)\1/g
const allowedTechnicalLiterals = new Set(['system'])

function productionFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) {
      return entry.name === 'i18n' || entry.name === 'test' ? [] : productionFiles(path)
    }
    return /\.(?:ts|tsx)$/.test(entry.name) && !/\.test\.(?:ts|tsx)$/.test(entry.name) ? [path] : []
  })
}

function lineAt(source: string, index: number): number {
  return source.slice(0, index).split('\n').length
}

describe('localized UI source guard', () => {
  it('keeps browser title free of hardcoded visible application copy', () => {
    const index = readFileSync(join(process.cwd(), 'index.html'), 'utf8')
    expect(index).not.toMatch(/<title>[^<]*\S[^<]*<\/title>/)
  })

  it('keeps German display literals inside locale JSON resources', () => {
    const issues = productionFiles(sourceRoot).flatMap((file) => {
      const source = readFileSync(file, 'utf8')
      return [...source.matchAll(stringLiteralPattern)].flatMap((match) => {
        const literal = match[2]
        return germanDisplayPattern.test(literal) && !allowedTechnicalLiterals.has(literal)
          ? [`${file}:${lineAt(source, match.index ?? 0)}: ${literal}`]
          : []
      })
    })

    expect(issues).toEqual([])
  })
})
