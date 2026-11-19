import { type ChangeEvent, useState } from 'react'
import { apiUrl, send } from '../api/client'
import type { AppContext } from '../app/context'
import { announceDataChanged } from '../lib/useLoad'

/** Largest file read in the browser for the row-checked kinds; the server refuses more anyway. */
const MAX_BYTES = 30_000_000

export type ImportKind = 'suppliers' | 'dishes' | 'staff' | 'invoices' | 'inventory' | 'orders' | 'punches' | 'payouts'
type ImportRow = { line: number | null; name: string | null; status: 'new' | 'update' | 'skip' | 'error'; messages: string[] }
type ImportReply = {
  kind: ImportKind | null
  label: string | null
  section: string | null
  confidence: number
  went_by: string[]
  needs_kind: boolean
  choices: { kind: ImportKind; label: string; section: string }[]
  saved: boolean
  rows: ImportRow[]
  new: number
  updated: number
  skipped: number
  errors: number
  ignored_columns?: string[]
  invoices?: number
  questions?: number
  left_out?: number
  orders?: number | null
  new_suppliers?: string[]
}

const upload = (csv: string, options: { kind?: ImportKind; updateExisting: boolean }) =>
  send<ImportReply>('POST', '/api/v1/imports', { csv, kind: options.kind ?? null, update_existing: options.updateExisting })

/** The kinds that have a template to download, and what each file holds. */
const TEMPLATES: Partial<Record<ImportKind, string>> = { suppliers: 'Suppliers', dishes: 'Dishes', staff: 'Team', invoices: 'Invoice lines' }

/**
 * Bring a restaurant's own data in from a spreadsheet. Choose a CSV and it is read at once: what the file holds is
 * worked out from its column names, every row is checked, and the rows are entered. Nothing is entered when a row
 * has a problem or when it is not clear what the file is; the panel then says what to fix or asks. The same file
 * twice changes nothing.
 */
export function CsvUpload({ app, kinds, what }: { app: AppContext; kinds: ImportKind[]; what: string }) {
  const [fileName, setFileName] = useState<string | null>(null)
  const [csv, setCsv] = useState<string | null>(null)
  const [updateExisting, setUpdateExisting] = useState(false)
  const [reply, setReply] = useState<ImportReply | null>(null)
  const [chosen, setChosen] = useState<ImportKind | ''>('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!app.canManage) return null

  const run = async (text: string, kind?: ImportKind, update = updateExisting) => {
    setBusy(true)
    setError(null)
    try {
      const result = await upload(text, { kind, updateExisting: update })
      setReply(result)
      if (result.saved) {
        announceDataChanged()
        app.refresh()
        const entered = result.new + result.updated
        app.toast(entered ? `${entered} ${result.label} entered` : 'Nothing new in that file', result.section ? `They are under ${result.section}.` : undefined)
      }
    } catch (err) {
      setReply(null)
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = '' // choosing the same file again should still re-read it
    if (!file) return
    setFileName(file.name)
    setReply(null)
    setChosen('')
    setCsv(null)
    if (/\.xlsx?$/i.test(file.name)) return setError('That is an Excel file. In Excel choose File, Save As, CSV, then pick the .csv file here.')
    if (file.size > MAX_BYTES) return setError('That file is over 30 MB. Split it into smaller files and upload each.')
    const text = await file.text()
    setCsv(text)
    // A file dropped in a section is read as what its columns say it is; one kind offered here settles an unclear file.
    await run(text)
  }

  const problems = reply ? reply.rows.filter((row) => row.status === 'error') : []
  const leftOut = reply?.saved ? reply.rows.filter((row) => row.messages.length > 0) : []

  return (
    <details className="card csv-upload">
      <summary>
        <span className="strong">Upload a CSV</span>
        <span className="muted small">{what}</span>
      </summary>
      <div className="card-body stack-sm">
        <p className="muted small">
          Save your spreadsheet as a CSV file and choose it. Columns are matched by name, the rows are checked, and they are entered straight away. A file with a problem row enters
          nothing until it is fixed. Uploading the same file twice changes nothing.
          {kinds.some((kind) => TEMPLATES[kind]) && ' Templates: '}
          {kinds
            .filter((kind) => TEMPLATES[kind])
            .map((kind, index) => (
              <span key={kind}>
                {index > 0 && ', '}
                <a href={apiUrl(`/api/v1/imports/${kind}/template`)} download={`fullhouse-${kind}-template.csv`}>
                  {TEMPLATES[kind]}
                </a>
              </span>
            ))}
          {kinds.some((kind) => TEMPLATES[kind]) && '.'}
        </p>
        <div className="row wrap">
          <label className="btn btn-secondary file-button">
            {fileName ? 'Choose another file' : 'Choose a CSV file'}
            <input type="file" accept=".csv,text/csv" className="visually-hidden" aria-label="Spreadsheet (CSV)" onChange={onFile} disabled={busy} />
          </label>
          {fileName && <span className="muted small">{fileName}</span>}
          <label className="row small">
            <input
              type="checkbox"
              checked={updateExisting}
              onChange={(e) => {
                setUpdateExisting(e.target.checked)
                if (csv && reply && !reply.needs_kind) void run(csv, reply.kind ?? undefined, e.target.checked)
              }}
            />
            Update rows that are already here
          </label>
        </div>

        {error && (
          <div className="auth-error" role="alert">
            {error}
          </div>
        )}
        {busy && <div className="muted small" role="status">Reading the file…</div>}

        {reply?.needs_kind && csv && (
          <div className="callout callout-warning" role="status">
            <div className="callout-grow">
              <div className="callout-title">Nothing was entered: the column names do not say clearly what this file is</div>
              <div className="row wrap" style={{ marginTop: 8 }}>
                <label className="visually-hidden" htmlFor="csv-kind">
                  What the file holds
                </label>
                <select id="csv-kind" className="input" style={{ width: 240 }} value={chosen} onChange={(e) => setChosen(e.target.value as ImportKind | '')}>
                  <option value="">This file holds…</option>
                  {reply.choices.map((choice) => (
                    <option key={choice.kind} value={choice.kind}>
                      {choice.label} ({choice.section})
                    </option>
                  ))}
                </select>
                <button className="btn btn-primary btn-sm" disabled={busy || !chosen} onClick={() => chosen && void run(csv, chosen)}>
                  Enter the rows
                </button>
              </div>
            </div>
          </div>
        )}

        {reply && !reply.needs_kind && !reply.saved && (
          <div className="stack-sm" aria-label="Rows to fix">
            <div className="callout callout-warning" role="status">
              <div className="callout-grow">
                <div className="callout-title">
                  Nothing was entered: {reply.errors} {reply.errors === 1 ? 'row needs' : 'rows need'} fixing in this file of {reply.label}
                </div>
                <div className="callout-body">
                  Fix the file and choose it again.{reply.rows.length - reply.errors > 0 && ` The other ${reply.rows.length - reply.errors === 1 ? 'row is' : `${reply.rows.length - reply.errors} rows are`} fine.`}
                </div>
              </div>
            </div>
            <div className="table-wrap import-table">
              <table className="table table-cards">
                <thead>
                  <tr>
                    <th>Line</th>
                    <th>Row</th>
                    <th>Problem</th>
                  </tr>
                </thead>
                <tbody>
                  {problems.map((row) => (
                    <tr key={row.line} className="row-error">
                      <td data-label="Line">{row.line}</td>
                      <td data-label="Row">{row.name ?? <span className="faint">(no name)</span>}</td>
                      <td data-label="Problem">{row.messages.join(' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {reply?.saved && (
          <div className="callout callout-info" role="status">
            <div className="callout-grow">
              <div className="callout-title">
                Read as {reply.label}: {reply.new} entered, {reply.updated} updated, {reply.skipped} already here
              </div>
              <div className="callout-body">
                {reply.section && `They are under ${reply.section}. `}
                {reply.invoices ? `${reply.invoices} ${reply.invoices === 1 ? 'invoice' : 'invoices'}. ` : ''}
                {reply.questions ? `${reply.questions} ${reply.questions === 1 ? 'line needs' : 'lines need'} an answer under Questions. ` : ''}
                {reply.new_suppliers?.length ? `New suppliers: ${reply.new_suppliers.join(', ')}. ` : ''}
                {reply.left_out ? `${reply.left_out} ${reply.left_out === 1 ? 'row was' : 'rows were'} left out because they could not be read. ` : ''}
                {reply.ignored_columns?.length ? `Columns not used: ${reply.ignored_columns.join(', ')}.` : ''}
              </div>
              {reply.left_out ? (
                <ul className="small" style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                  {leftOut.slice(0, 8).map((row, index) => (
                    <li key={index}>
                      Line {row.line}: {row.messages.join(' ')}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </div>
        )}
      </div>
    </details>
  )
}
