// The interface-text lint for the Margin & Labor pages (spec section 10.5). It reads the words a person would
// see in src/ml (string literals and JSX text) and fails the build on a banned word, an exclamation mark, an
// emoji, a greeting or apology, or an internal term that has a plain one. Existing pages are not checked.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const ROOT = new URL('../src/ml/', import.meta.url).pathname
const BANNED = ['smart', 'powerful', 'seamless', 'insights', 'optimize', 'unlock', 'leverage', 'ai-powered', 'magic', 'effortless', 'robust', 'delight']
const SAY_INSTEAD = { 'theoretical usage': 'Expected use', variance: 'Unexplained use', p50: 'Expected', p10: 'Likely range', p90: 'Likely range', wape: 'Average miss',
  'unit contribution': 'Kept per item', 'plate cost': 'Cost per plate', need_p50: 'People needed', need_p90: 'People needed if busy' }
const FORMS = [[/!(?!=)/, 'an exclamation mark'], [/\p{Extended_Pictographic}/u, 'an emoji'], [/\b(welcome|hello|hi there|sorry|oops|great job|well done|congratulations)\b/i, 'a greeting, praise or an apology']]

function files(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? files(path) : /\.tsx?$/.test(name) ? [path] : []
  })
}

/** The text a person sees: quoted strings and JSX text, with comments, imports, class names and code left out. */
export function visibleText(source) {
  const out = []
  const code = source.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' ')).replace(/(^|[^:])\/\/.*$/gm, (m, lead) => lead + ' '.repeat(m.length - lead.length))
  code.split('\n').forEach((line, index) => {
    if (/^\s*(import|export type|type )\b/.test(line)) return
    const kept = line.replace(/\b(className|data-testid|key|role|href|accept|id|stroke\w*|fill|d|transform|textAnchor|kind|name)=(\{`[^`]*`\}|"[^"]*"|\{'[^']*'\})/g, '')
    for (const m of kept.matchAll(/'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`|>([^<>{}]+)</g)) {
      const text = (m[1] ?? m[2] ?? m[3] ?? m[4] ?? '').trim()
      // Words a person reads have a space or start with a capital; identifiers, paths, units and CSS do not.
      if (text && (/\s/.test(text) || /^[A-Z][a-z]/.test(text)) && !/^[\w./-]+$/.test(text.replace(/\s/g, '') + (/\s/.test(text) ? ' ' : ''))) out.push({ line: index + 1, text })
    }
  })
  return out
}

export function problems(source) {
  const found = []
  for (const { line, text } of visibleText(source)) {
    const lower = text.toLowerCase()
    for (const word of BANNED) if (new RegExp(`\\b${word}\\b`).test(lower)) found.push({ line, text, why: `the banned word "${word}"` })
    for (const [term, plain] of Object.entries(SAY_INSTEAD)) if (new RegExp(`\\b${term}\\b`).test(lower)) found.push({ line, text, why: `"${term}": say "${plain}"` })
    for (const [pattern, what] of FORMS) if (pattern.test(text)) found.push({ line, text, why: what })
  }
  return found
}

if (process.argv[1] === new URL(import.meta.url).pathname) {
  // The lint proves itself first: it must catch a planted line and pass a clean one.
  const planted = problems(`const a = <p>Unlock powerful insights!</p>\nconst b = 'The variance was high'`)
  if (planted.length < 5 || problems(`const a = <p>Chicken costs $212 more a week.</p>`).length) {
    console.error('lint-ml-text: the self-check failed; the lint is not catching what it should.')
    process.exit(2)
  }
  let failed = 0
  for (const path of files(ROOT)) {
    for (const p of problems(readFileSync(path, 'utf8'))) {
      console.error(`${path.replace(ROOT, 'src/ml/')}:${p.line}  ${p.why}  ->  ${p.text.slice(0, 80)}`)
      failed += 1
    }
  }
  if (failed) {
    console.error(`\n${failed} interface-text problem${failed === 1 ? '' : 's'} (docs/SPEC-margin-labor.md, section 10.5).`)
    process.exit(1)
  }
  console.log('lint-ml-text: the Margin & Labor pages keep the writing rules.')
}
